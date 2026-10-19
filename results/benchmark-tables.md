# Measured benchmark results

Each row is one measured run. N/A means unavailable, never zero.

| Configuration | Trace | Warmups | Topology | Policy | C | Run | OK/total | Req/s | Output tok/s | TTFT p50/p95 s | E2E p50/p95 s | Cached prompt ratio |
|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|

## Paired cache-aware throughput changes

| Configuration | Trace | Warmups | Topology | C | Run | Output tok/s change vs round-robin |
|---|---|---:|---|---:|---:|---:|

## Medians across repeat runs

Run-level medians are not pooled-request latency percentiles. Inspect raw failures before selecting a winner.

| Configuration | Trace | Warmups | Topology | Policy | C | Runs | Failures | Output tok/s median [min,max] | TTFT p95 median s | E2E p95 median s |
|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|

PENDING: no hardware benchmark runs have been recorded.
