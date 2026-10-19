import argparse
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bench"))
sys.path.insert(0, str(ROOT / "discovery"))
from benchmark import infer, percentile, run, summarize
from make_workload import make_record
from reconcile import Reconciler, desired_workers
from report import report


def service(address="10.0.0.5", tags=None):
    return {
        "Node": {"Address": address},
        "Service": {
            "Address": "",
            "Port": 30001,
            "Tags": ["sglang", "model=chat-default"] if tags is None else tags,
        },
    }


class DiscoveryTests(unittest.TestCase):
    def test_address_fallback_and_model(self):
        self.assertEqual(
            desired_workers([service()]), {"http://10.0.0.5:30001": "chat-default"}
        )

    def test_ipv6(self):
        self.assertIn("http://[::1]:30001", desired_workers([service("::1")]))

    def test_ambiguous_model_preserves_registry(self):
        with self.assertRaises(ValueError):
            desired_workers([service(tags=["sglang", "model=a", "model=b"])])

    def test_async_add_does_not_repeat_each_poll(self):
        r = Reconciler()
        desired = desired_workers([service()])
        self.assertEqual(len(r.actions(desired, [], 0)), 1)
        self.assertEqual(r.actions(desired, [], 2), [])
        self.assertEqual(len(r.actions(desired, [], 61)), 1)

    def test_remove_only_after_two_successful_absent_snapshots(self):
        r = Reconciler()
        worker = {
            "url": "http://old:30001",
            "id": "old-uuid",
            "model_id": "chat-default",
        }
        self.assertEqual(r.actions({}, [worker], 0), [])
        self.assertEqual(
            r.actions({}, [worker], 2)[0][:2], ("DELETE", "/workers/old-uuid")
        )

    def test_present_worker_clears_absence(self):
        r = Reconciler()
        worker = {"url": "http://old:30001", "id": "old-uuid"}
        r.actions({}, [worker], 0)
        r.actions({worker["url"]: "chat-default"}, [worker], 2)
        self.assertEqual(r.actions({}, [worker], 4), [])

    def test_model_mismatch_stops_mutation(self):
        with self.assertRaises(ValueError):
            Reconciler().actions(
                {"http://worker:1": "a"},
                [{"url": "http://worker:1", "model_id": "b", "id": "id"}],
                0,
            )


class SummaryTests(unittest.TestCase):
    def test_missing_cache_is_unknown(self):
        row = {
            "ok": True,
            "ttft_s": 1,
            "e2e_s": 2,
            "usage": {"prompt_tokens": 100, "completion_tokens": 10},
        }
        self.assertIsNone(summarize([row], 2)["cache_hit_rate"])

    def test_cache_is_token_weighted(self):
        rows = [
            {
                "ok": True,
                "ttft_s": 1,
                "e2e_s": 2,
                "usage": {
                    "prompt_tokens": p,
                    "completion_tokens": 10,
                    "prompt_tokens_details": {"cached_tokens": c},
                },
            }
            for p, c in [(100, 100), (900, 0)]
        ]
        self.assertEqual(summarize(rows, 2)["cache_hit_rate"], 0.1)
        self.assertEqual(percentile([1, 3], 0.5), 2)

    def test_empty_report_cannot_invent_hardware_results(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertIn("PENDING", report(d))


class StreamingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mode = "ok"
        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.stream)

        async def flush(_):
            return web.json_response({"ok": True})

        async def workers(_):
            return web.json_response({"workers": []})

        async def metrics(_):
            return web.Response(text='smg_worker_requests_active{worker="mock"} 1\n')

        app.router.add_post("/flush_cache", flush)
        app.router.add_get("/workers", workers)
        app.router.add_get("/metrics", metrics)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        self.args = argparse.Namespace(
            model="chat-default", max_tokens=8, fixed_output=False
        )

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def stream(self, request):
        await request.json()
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        events = [
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"delta": {"content": "Hello"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ]
        if self.mode != "missing-usage":
            events.append(
                {
                    "choices": [],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 1,
                        "prompt_tokens_details": {"cached_tokens": 80},
                    },
                }
            )
        for event in events:
            await response.write(("data: " + json.dumps(event) + "\n\n").encode())
            await asyncio.sleep(0.001)
        if self.mode != "truncated":
            await response.write(b"data: [DONE]\n\n")
        await response.write_eof()
        return response

    async def test_stream_usage_and_printable_ttft(self):
        async with aiohttp.ClientSession() as session:
            row = await infer(session, self.base, make_record(0), self.args, "test", 0)
        self.assertTrue(row["ok"])
        self.assertEqual(row["output"], "Hello")
        self.assertEqual(row["usage"]["prompt_tokens_details"]["cached_tokens"], 80)
        self.assertGreater(row["ttft_s"], 0)

    async def test_truncated_stream_is_failure(self):
        self.mode = "truncated"
        async with aiohttp.ClientSession() as session:
            row = await infer(session, self.base, make_record(0), self.args, "test", 0)
        self.assertFalse(row["ok"])

    async def test_missing_usage_is_failure(self):
        self.mode = "missing-usage"
        async with aiohttp.ClientSession() as session:
            row = await infer(session, self.base, make_record(0), self.args, "test", 0)
        self.assertFalse(row["ok"])

    async def test_full_runner_preserves_raw_results(self):
        with tempfile.TemporaryDirectory() as d:
            folder = Path(d)
            trace = folder / "trace.jsonl"
            trace.write_text(
                "".join(json.dumps(make_record(i)) + "\n" for i in range(8))
            )
            manifest = folder / "manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        k: "LOCAL_MOCK_ONLY"
                        for k in [
                            "worker_image",
                            "gateway_image",
                            "model_revision",
                            "gpu_inventory",
                            "worker_args",
                            "gateway_args",
                        ]
                    }
                )
            )
            args = argparse.Namespace(
                **vars(self.args),
                url=self.base,
                output=str(folder / "run"),
                workload=str(trace),
                manifest=str(manifest),
                configuration="mock-only",
                requests=8,
                warmup=1,
                flush=True,
                timeout=10,
                concurrency=8,
                metrics_url=self.base,
                worker_url=[],
                topology="replicas",
                policy="cache_aware",
                repeat=1,
            )
            self.assertEqual(await run(args), 0)
            summary = json.loads((folder / "run/summary.json").read_text())
            self.assertEqual(summary["successful"], 8)
            self.assertEqual(summary["cache_hit_rate"], 0.8)
            self.assertEqual(
                len((folder / "run/requests.jsonl").read_text().splitlines()), 8
            )
            self.assertTrue((folder / "run/gateway-metrics-timeseries.jsonl").exists())
            self.assertIn("mock-only", report(folder))


if __name__ == "__main__":
    unittest.main()
