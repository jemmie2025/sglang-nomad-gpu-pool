"""Streaming closed-loop benchmark. Saves every request, timings and usage.

No client retries: gateway failures cannot be concealed. Synthetic prompts only.
TTFT is time until first printable content/reasoning, rather than an SSE role event.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

import aiohttp


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * q
    lo = math.floor(position)
    hi = math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def summarize(rows, elapsed):
    good = [r for r in rows if r["ok"]]
    prompts = sum(r["usage"]["prompt_tokens"] for r in good)
    complete_cache = all(
        isinstance(
            r["usage"].get("prompt_tokens_details", {}).get("cached_tokens"), int
        )
        for r in good
    )
    cached = sum(
        r["usage"].get("prompt_tokens_details", {}).get("cached_tokens", 0)
        for r in good
    )
    return {
        "requests": len(rows),
        "successful": len(good),
        "failed": len(rows) - len(good),
        "elapsed_s": elapsed,
        "successful_requests_s": len(good) / elapsed,
        "output_tokens_s": sum(r["usage"]["completion_tokens"] for r in good) / elapsed,
        "prompt_tokens": prompts,
        "cache_hit_rate": cached / prompts if prompts and complete_cache else None,
        "cache_definition": "sum(cached prompt tokens) / sum(prompt tokens), successful requests",
        "ttft_p50_s": percentile(
            [r["ttft_s"] for r in good if r["ttft_s"] is not None], 0.5
        ),
        "ttft_p95_s": percentile(
            [r["ttft_s"] for r in good if r["ttft_s"] is not None], 0.95
        ),
        "e2e_p50_s": percentile([r["e2e_s"] for r in good], 0.5),
        "e2e_p95_s": percentile([r["e2e_s"] for r in good], 0.95),
    }


async def infer(session, base, record, args, phase, index):
    started = time.monotonic()
    row = {
        "phase": phase,
        "index": index,
        "start_epoch": time.time(),
        "ok": False,
        "status": None,
        "usage": {},
        "ttft_s": None,
        "finish_reason": None,
        "output": "",
        "events": [],
    }
    payload = {
        **record,
        "model": args.model,
        "temperature": 0,
        "max_tokens": args.max_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if args.fixed_output:
        payload["ignore_eos"] = True
    try:
        async with session.post(
            base + "/v1/chat/completions", json=payload
        ) as response:
            row["status"] = response.status
            if response.status != 200:
                raise RuntimeError((await response.text())[:2000])
            done = False
            async for line in response.content:
                text = line.decode().strip()
                if not text.startswith("data:"):
                    continue
                data = text[5:].strip()
                if data == "[DONE]":
                    done = True
                    break
                event = json.loads(data)
                row["events"].append(event)
                if event.get("error"):
                    raise RuntimeError(str(event["error"]))
                if event.get("usage"):
                    row["usage"] = event["usage"]
                for choice in event.get("choices", []):
                    delta = choice.get("delta", {})
                    output = (
                        delta.get("content") or delta.get("reasoning_content") or ""
                    )
                    if output and row["ttft_s"] is None:
                        row["ttft_s"] = time.monotonic() - started
                    row["output"] += output
                    if choice.get("finish_reason"):
                        row["finish_reason"] = choice["finish_reason"]
            if not done or row["finish_reason"] not in ("stop", "length"):
                raise RuntimeError("Incomplete or aborted stream")
            if not all(
                isinstance(row["usage"].get(k), int)
                for k in ("prompt_tokens", "completion_tokens")
            ):
                raise RuntimeError("Missing final token usage")
            row["ok"] = True
    except (
        aiohttp.ClientError,
        TimeoutError,
        ValueError,
        RuntimeError,
        KeyError,
        TypeError,
    ) as error:
        row["error"] = str(error)
    row["e2e_s"] = time.monotonic() - started
    row["end_epoch"] = time.time()
    return row


async def snapshot(session, args, out, name):
    targets = {"registry": args.url.rstrip("/") + "/workers"}
    if args.metrics_url:
        targets["gateway_metrics"] = args.metrics_url.rstrip("/") + "/metrics"
    targets.update(
        {
            f"worker_{i}": u.rstrip("/") + "/metrics"
            for i, u in enumerate(args.worker_url)
        }
    )
    for key, url in targets.items():
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as response:
                body = await response.text()
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}: {body[:200]}")
                (out / f"{name}-{key}.txt").write_text(body)
        except (aiohttp.ClientError, TimeoutError, OSError, RuntimeError) as error:
            (out / f"{name}-{key}-error.txt").write_text(str(error))


async def sample_metrics(session, args, out, stop):
    while not stop.is_set():
        row = {"epoch": time.time()}
        try:
            async with session.get(
                args.metrics_url.rstrip("/") + "/metrics",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as response:
                row["status"] = response.status
                row["metrics"] = await response.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as error:
            row["error"] = str(error)
        with (out / "gateway-metrics-timeseries.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        try:
            await asyncio.wait_for(stop.wait(), timeout=1)
        except TimeoutError:
            pass


async def run(args):
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    trace = Path(args.workload).read_bytes()
    records = [json.loads(line) for line in trace.decode().splitlines() if line.strip()]
    if len(records) < args.requests:
        raise ValueError("Workload shorter than requested measured run")
    manifest = json.loads(Path(args.manifest).read_text())
    required = (
        "worker_image",
        "gateway_image",
        "model_revision",
        "gpu_inventory",
        "worker_args",
        "gateway_args",
    )
    if any(not manifest.get(k) for k in required) or "REPLACE" in json.dumps(manifest):
        raise ValueError("Complete the environment manifest with observed values")
    metadata = {
        **vars(args),
        "environment": manifest,
        "started_utc": datetime.now(UTC).isoformat(),
        "trace_sha256": hashlib.sha256(trace).hexdigest(),
        "python": platform.python_version(),
        "aiohttp": aiohttp.__version__,
        "client_platform": platform.platform(),
        "client_retries": 0,
        "measured_request_order": "fixed trace; concurrency controls completion order",
    }
    (out / "metadata.json").write_text(json.dumps(metadata, indent=2))
    (out / "workload.jsonl").write_bytes(trace)
    headers = (
        {"Authorization": "Bearer " + os.environ["BENCH_API_KEY"]}
        if os.environ.get("BENCH_API_KEY")
        else {}
    )
    timeout = aiohttp.ClientTimeout(total=args.timeout)
    async with aiohttp.ClientSession(
        headers=headers,
        timeout=timeout,
        connector=aiohttp.TCPConnector(limit=args.concurrency + 4),
    ) as session:
        if args.flush:
            async with session.post(args.url.rstrip("/") + "/flush_cache") as response:
                body = await response.text()
                (out / "flush-response.txt").write_text(body)
                if response.status != 200:
                    raise RuntimeError("Cache flush failed; refuse incomparable run")
            # Inspect flush-response.txt against the pinned gateway schema before publication.
        warmups = []
        for i in range(args.warmup):
            warmups.append(
                await infer(
                    session,
                    args.url.rstrip("/"),
                    records[i % len(records)],
                    args,
                    "warmup",
                    i,
                )
            )
        (out / "warmup.jsonl").write_text(
            "".join(json.dumps(r) + "\n" for r in warmups)
        )
        if any(not r["ok"] for r in warmups):
            raise RuntimeError("Warmup failed; inspect warmup.jsonl")
        await snapshot(session, args, out, "before")
        queue = asyncio.Queue()
        for i, record in enumerate(records[: args.requests]):
            queue.put_nowait((i, record))
        rows = []

        async def consumer():
            while not queue.empty():
                i, record = queue.get_nowait()
                row = await infer(
                    session, args.url.rstrip("/"), record, args, "measured", i
                )
                rows.append(row)
                # Append on completion so a client crash preserves completed requests.
                with (out / "requests.jsonl").open("a") as stream:
                    stream.write(json.dumps(row) + "\n")
                queue.task_done()

        started = time.monotonic()
        stop = asyncio.Event()
        sampler = asyncio.create_task(sample_metrics(session, args, out, stop))
        try:
            await asyncio.gather(*(consumer() for _ in range(args.concurrency)))
            elapsed = time.monotonic() - started
        finally:
            stop.set()
            await sampler
        await snapshot(session, args, out, "after")
    summary = {
        "configuration": args.configuration,
        "trace_sha256": metadata["trace_sha256"],
        "max_tokens": args.max_tokens,
        "fixed_output": args.fixed_output,
        "warmup": args.warmup,
        "topology": args.topology,
        "policy": args.policy,
        "concurrency": args.concurrency,
        "repeat": args.repeat,
        "model": args.model,
        **summarize(rows, elapsed),
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 1 if summary["failed"] else 0


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True, help="Gateway root URL, WITHOUT /v1")
    p.add_argument(
        "--configuration",
        required=True,
        help="Distinct model/quantization configuration label",
    )
    p.add_argument(
        "--manifest",
        required=True,
        help="Observed environment JSON; see manifest.example.json",
    )
    p.add_argument("--model", default="chat-default")
    p.add_argument("--workload", default="bench/workload.jsonl")
    p.add_argument("--output", required=True, help="A new run directory")
    p.add_argument("--concurrency", type=int, choices=[1, 8, 32], required=True)
    p.add_argument("--topology", choices=["single", "replicas", "tp2"], required=True)
    p.add_argument("--policy", choices=["cache_aware", "round_robin"], required=True)
    p.add_argument("--requests", type=int, default=256)
    p.add_argument("--warmup", type=int, default=32)
    p.add_argument("--max-tokens", type=int, default=128)
    p.add_argument("--timeout", type=float, default=300)
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--flush", action="store_true")
    p.add_argument(
        "--fixed-output",
        action="store_true",
        help="SGLang ignore_eos extension; use identically in all comparisons",
    )
    p.add_argument("--metrics-url", required=True)
    p.add_argument("--worker-url", action="append", default=[])
    return p


if __name__ == "__main__":
    arguments = parser().parse_args()
    if arguments.requests <= 0 or arguments.warmup < 0 or arguments.max_tokens <= 0:
        raise SystemExit("Invalid request/token counts")
    raise SystemExit(asyncio.run(run(arguments)))
