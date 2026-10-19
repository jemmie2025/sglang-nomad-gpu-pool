"""Destructive LAB test: kills container PID 1 in exactly the nominated allocation.

The nominated task must be the GPU worker, never a gateway or unrelated workload.
Nomad task events and registry snapshots require operator review before acceptance.
"""

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import aiohttp
from benchmark import infer, snapshot


async def command(*argv):
    p = await asyncio.create_subprocess_exec(
        *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await p.communicate()
    return {
        "returncode": p.returncode,
        "stdout": stdout.decode(),
        "stderr": stderr.decode(),
    }


async def run(args):
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    records = [
        json.loads(line) for line in Path(args.workload).read_text().splitlines()
    ]
    baseline = await command("nomad", "alloc", "status", "-json", args.allocation)
    (out / "nomad-before.json").write_text(json.dumps(baseline, indent=2))
    if baseline["returncode"]:
        raise RuntimeError("Cannot inspect nominated allocation")
    allocation = json.loads(baseline["stdout"])
    tasks = allocation["Job"]["TaskGroups"]
    group = next(g for g in tasks if g["Name"] == allocation["TaskGroup"])
    worker = next(t for t in group["Tasks"] if t["Name"] == "worker")
    # Prevent accidental kill outside the supplied worker job family.
    if (
        not allocation["JobID"].startswith("sglang-")
        or worker.get("Driver") != "docker"
    ):
        raise RuntimeError("Target is not an SGLang Docker worker allocation")
    headers = (
        {"Authorization": "Bearer " + os.environ["BENCH_API_KEY"]}
        if os.environ.get("BENCH_API_KEY")
        else {}
    )
    rows = []
    kill_event = {}
    started = time.monotonic()
    async with aiohttp.ClientSession(
        headers=headers, timeout=aiohttp.ClientTimeout(total=args.timeout)
    ) as session:
        await snapshot(session, args, out, "before")

        async def load(slot):
            i = slot
            while time.monotonic() - started < args.duration:
                row = await infer(
                    session,
                    args.url.rstrip("/"),
                    records[i % len(records)],
                    args,
                    "failure",
                    i,
                )
                rows.append(row)
                with (out / "requests.jsonl").open("a") as f:
                    f.write(json.dumps(row) + "\n")
                i += args.concurrency

        async def kill():
            await asyncio.sleep(args.kill_after)
            kill_event["command_start_epoch"] = time.time()
            result = await command(
                "nomad",
                "alloc",
                "exec",
                "-task",
                "worker",
                args.allocation,
                "python3",
                "-c",
                "import os,signal; os.kill(1,signal.SIGKILL)",
            )
            kill_event["command_return_epoch"] = time.time()
            kill_event["result"] = result
            (out / "kill-event.json").write_text(json.dumps(kill_event, indent=2))
            await snapshot(session, args, out, "immediately-after-kill")

        await asyncio.gather(kill(), *(load(i) for i in range(args.concurrency)))
        await snapshot(session, args, out, "after")
    after = await command("nomad", "alloc", "status", "-json", args.allocation)
    (out / "nomad-after.json").write_text(json.dumps(after, indent=2))
    job_allocs = await command("nomad", "job", "allocs", "-json", allocation["JobID"])
    (out / "job-allocations-after.json").write_text(json.dumps(job_allocs, indent=2))
    before_t, after_t = (
        kill_event["command_start_epoch"],
        kill_event["command_return_epoch"],
    )
    failed = [r for r in rows if not r["ok"]]
    post = [r for r in rows if r["start_epoch"] >= after_t]
    prior_failures = [r for r in failed if r["end_epoch"] < before_t]
    post_failures = [r for r in post if not r["ok"]]
    ambiguous = [r for r in failed if before_t <= r["start_epoch"] < after_t]
    summary = {
        "requests": len(rows),
        "failed": len(failed),
        "baseline_failures": len(prior_failures),
        "confirmed_post_kill_requests": len(post),
        "confirmed_post_kill_failures": len(post_failures),
        "failures_started_in_kill_command_interval": len(ambiguous),
        "client_check": "PASS"
        if post and not prior_failures and not post_failures and not ambiguous
        else "FAIL_OR_INCONCLUSIVE",
        "overall_acceptance": "PENDING_OPERATOR_REVIEW",
        "review_required": "Confirm target process died, restart count/events changed, worker became healthy and both workers resumed serving; correlate exact kill time.",
    }
    (out / "failure-summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0 if summary["client_check"] == "PASS" else 1


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True)
    p.add_argument("--allocation", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--model", default="chat-default")
    p.add_argument("--workload", default="bench/workload.jsonl")
    p.add_argument("--concurrency", type=int, choices=[1, 8, 32], default=8)
    p.add_argument("--duration", type=float, default=180)
    p.add_argument("--kill-after", type=float, default=20)
    p.add_argument("--timeout", type=float, default=300)
    p.add_argument("--max-tokens", type=int, default=64)
    p.add_argument("--fixed-output", action="store_true")
    p.add_argument("--metrics-url")
    p.add_argument("--worker-url", action="append", default=[])
    a = p.parse_args()
    if not 0 < a.kill_after < a.duration or a.max_tokens <= 0:
        raise SystemExit("kill-after must be within a positive test duration")
    raise SystemExit(asyncio.run(run(a)))
