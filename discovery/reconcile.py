"""Consul passing services -> gateway /workers API. Python standard library only.

Run exactly one controller per gateway. Gateway registry is the source of actual
state, so restarting either process does not require local persistent state.
"""

import json
import logging
import os
import time
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

LOG = logging.getLogger("discovery")


def request(base, path, method="GET", body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["X-Consul-Token"] = token
    req = Request(
        base.rstrip("/") + path,
        method=method,
        headers=headers,
        data=None if body is None else json.dumps(body).encode(),
    )
    with urlopen(req, timeout=10) as response:
        if (
            response.headers.get("X-Consul-Results-Filtered-By-ACLs", "false").lower()
            == "true"
        ):
            raise RuntimeError("Consul ACL filtered the result; preserve registry")
        raw = response.read()
        return json.loads(raw) if raw else {}


def desired_workers(services):
    desired = {}
    for entry in services:
        service = entry["Service"]
        tags = service.get("Tags") or []
        # This controller manages only explicitly tagged SGLang services.
        if "sglang" not in tags:
            continue
        models = [t.split("=", 1)[1] for t in tags if t.startswith("model=")]
        if len(models) != 1 or not models[0]:
            raise ValueError("Missing or ambiguous model tag; preserve registry")
        address = service.get("Address") or entry["Node"]["Address"]
        if not address or not 0 < int(service["Port"]) < 65536:
            raise ValueError("Invalid Consul worker address")
        host = f"[{address}]" if ":" in address else address
        desired[f"http://{host}:{service['Port']}"] = models[0]
    return desired


class Reconciler:
    def __init__(self):
        self.absent = {}
        self.next_add = {}

    def actions(self, desired, workers, now):
        actions = []
        existing = {w["url"].rstrip("/"): w for w in workers}
        for url, model in desired.items():
            self.absent.pop(url, None)
            if url in existing:
                actual = existing[url].get("model_id")
                if actual and actual != model:
                    raise ValueError(f"Model mismatch for {url}: {actual} != {model}")
                self.next_add.pop(url, None)
            elif now >= self.next_add.get(url, 0):
                # Registration is asynchronous. Avoid duplicate POST every poll.
                actions.append(("POST", "/workers", {"url": url, "model_id": model}))
                self.next_add[url] = now + 60
        for url, worker in existing.items():
            if url in desired:
                continue
            self.absent[url] = self.absent.get(url, 0) + 1
            if self.absent[url] >= 2:
                actions.append(
                    ("DELETE", "/workers/" + quote(worker["id"], safe=""), None)
                )
        return actions


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    consul = os.environ["CONSUL_HTTP_ADDR"]
    gateway = os.environ["GATEWAY_URL"]
    token = os.environ.get("CONSUL_HTTP_TOKEN")
    path = "/v1/health/service/" + quote(
        os.environ.get("WORKER_SERVICE", "sglang-worker"), safe=""
    )
    path += "?" + urlencode({"passing": "true"})
    controller = Reconciler()
    while True:
        try:
            desired = desired_workers(request(consul, path, token=token))
            registry = request(gateway, "/workers")
            if not isinstance(registry.get("workers"), list):
                raise TypeError("Unsupported gateway /workers response schema")
            for method, route, body in controller.actions(
                desired, registry["workers"], time.monotonic()
            ):
                result = request(gateway, route, method, body)
                LOG.info("%s %s %s", method, route, result)
        except Exception:
            # Never interpret a Consul/API error as an empty worker set.
            controller.absent.clear()
            LOG.exception("Reconciliation failed; preserving existing registry")
        time.sleep(float(os.environ.get("POLL_SECONDS", "2")))


if __name__ == "__main__":
    main()
