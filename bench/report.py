"""Render benchmark tables only from recorded summary.json files."""

import argparse
import json
from collections import defaultdict
from pathlib import Path
from statistics import median


def fmt(value):
    return "N/A" if value is None else f"{value:.3f}"


def report(root):
    rows = [json.loads(p.read_text()) for p in Path(root).glob("**/summary.json")]
    rows.sort(
        key=lambda r: (
            r["configuration"],
            r["topology"],
            r["policy"],
            r["concurrency"],
            r["repeat"],
        )
    )
    lines = [
        "# Measured benchmark results",
        "",
        "Each row is one measured run. N/A means unavailable, never zero.",
        "",
        "| Configuration | Trace | Warmups | Topology | Policy | C | Run | OK/total | Req/s | Output tok/s | TTFT p50/p95 s | E2E p50/p95 s | Cached prompt ratio |",
        "|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in rows:
        lines.append(
            f"| {r['configuration']} | {r['trace_sha256'][:8]} | {r['warmup']} | {r['topology']} | {r['policy']} | {r['concurrency']} | {r['repeat']} | "
            f"{r['successful']}/{r['requests']} | {fmt(r['successful_requests_s'])} | {fmt(r['output_tokens_s'])} | "
            f"{fmt(r['ttft_p50_s'])}/{fmt(r['ttft_p95_s'])} | {fmt(r['e2e_p50_s'])}/{fmt(r['e2e_p95_s'])} | {fmt(r['cache_hit_rate'])} |"
        )
    lines += [
        "",
        "## Paired cache-aware throughput changes",
        "",
        "| Configuration | Trace | Warmups | Topology | C | Run | Output tok/s change vs round-robin |",
        "|---|---|---:|---|---:|---:|---:|",
    ]

    def key(r, policy):
        return (
            r["configuration"],
            r["model"],
            r["trace_sha256"],
            r["max_tokens"],
            r["fixed_output"],
            r["warmup"],
            r["topology"],
            r["concurrency"],
            r["repeat"],
            policy,
        )

    index = {}
    for r in rows:
        identity = key(r, r["policy"])
        if identity in index:
            raise ValueError(
                "Duplicate experiment/repeat identity; use a distinct configuration label"
            )
        index[identity] = r
    for r in rows:
        if r["policy"] != "cache_aware":
            continue
        baseline = index.get(key(r, "round_robin"))
        if (
            baseline
            and baseline["output_tokens_s"]
            and not r["failed"]
            and not baseline["failed"]
        ):
            delta = 100 * (r["output_tokens_s"] / baseline["output_tokens_s"] - 1)
            lines.append(
                f"| {r['configuration']} | {r['trace_sha256'][:8]} | {r['warmup']} | {r['topology']} | {r['concurrency']} | {r['repeat']} | {delta:+.2f}% |"
            )
    lines += [
        "",
        "## Medians across repeat runs",
        "",
        "Run-level medians are not pooled-request latency percentiles. Inspect raw failures before selecting a winner.",
        "",
        "| Configuration | Trace | Warmups | Topology | Policy | C | Runs | Failures | Output tok/s median [min,max] | TTFT p95 median s | E2E p95 median s |",
        "|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    groups = defaultdict(list)
    for r in rows:
        # All comparison settings except repeat number remain identical.
        groups[key(r, r["policy"])[:8] + (r["policy"],)].append(r)
    for group in groups.values():
        r = group[0]
        rates = [g["output_tokens_s"] for g in group]

        def med(field, group=group):
            values = [g[field] for g in group if g[field] is not None]
            return fmt(median(values)) if values else "N/A"

        lines.append(
            f"| {r['configuration']} | {r['trace_sha256'][:8]} | {r['warmup']} | {r['topology']} | {r['policy']} | {r['concurrency']} | "
            f"{len(group)} | {sum(g['failed'] for g in group)} | {fmt(median(rates))} [{fmt(min(rates))},{fmt(max(rates))}] | {med('ttft_p95_s')} | {med('e2e_p95_s')} |"
        )
    if not rows:
        lines += ["", "PENDING: no hardware benchmark runs have been recorded."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--results", default="results")
    p.add_argument("--output", default="results/benchmark-tables.md")
    a = p.parse_args()
    Path(a.output).write_text(report(a.results))
    print(a.output)
