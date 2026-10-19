# Proxmox GPU pool: SGLang + Nomad + Consul

Implementation and validation pack for gpu-01 on G6326B, initially two RTX 3060
12 GB cards, expanding to 10-12 GPUs across G6326A/G6326B.

**Status: prepared for cluster validation. Hardware acceptance is pending.**
No performance measurements, successful deployments or n8n executions are
claimed by this pack. See [evidence status](results/STATUS.md).

## Start here

1. Read [the three-page design](docs/design.pdf) and [runbook](docs/RUNBOOK.md).
2. Collect the Day 1 inventory in the runbook. Confirm passthrough, driver,
   GPU visibility, software versions and routed addresses before deployment.
3. Run one worker manually and save a curl completion. Continue only when it passes.
4. Validate two workers and the gateway, then run the experiment matrix.
5. Deploy the Nomad jobs, test discovery and worker failure, then test n8n.
6. Fill the measured tables and review the design with the requester.

## Deliverables

| File | Purpose |
|---|---|
| `nomad/workers.nomad.hcl` | Chat pool; one reserved GPU per allocation by default |
| `nomad/gateway.nomad.hcl` | Consul-registered gateway and discovery task |
| `discovery/reconcile.py` | Reconcile passing Consul workers through gateway API |
| `bench/benchmark.py` | Streaming requests, raw events, TTFT, E2E, throughput, cache usage |
| `bench/report.py` | Tables and paired cache-aware throughput changes |
| `bench/failure.py` | Controlled worker kill with client and Nomad evidence |
| `bench/link-test.sh` | Routed-link RTT and bidirectional TCP throughput |
| `docs/design.pdf` | Three-page design, placement, discovery decision and risks |
| `docs/RUNBOOK.md` | Exact sequence, prerequisites, experiments and acceptance |

## Important deployment choices

- Pin worker/gateway/container images by tested digest and model checkpoints by
  commit. Examples deliberately contain required replacement values.
- A task `device.count = 1` reserves one GPU. Group `count = 2` creates two
  allocations. Group count does not automatically track installed GPUs.
- Match group count to eligible GPUs **within each model pool**, rather than
  assigning the entire mixed fleet to chat. Keep `NVIDIA_VISIBLE_DEVICES` under
  Nomad's device plugin control; never set it to `all`.
- Worker jobs use host networking and dynamically assigned ports. Route and
  firewall those addresses between clients, gateway and Consul agents.
- Models are pre-cached into a read-only host volume on every eligible client.
  This avoids internet downloads during worker recovery.
- The baseline uses FP16 KV and Triton attention on Ampere; Marlin concerns the
  quantized linear layers, independently of the attention backend.
- The gateway has no GPU reservation. The initial single gateway remains a
  single point of failure; gateway HA is a later design decision.

## Local verification

```bash
python3 -m pip install -r bench/requirements.txt
python3 -m unittest discover -s tests -v
python3 bench/make_workload.py
python3 bench/report.py
```

Tests use a local fake streaming endpoint and deterministic discovery fixtures.
Benchmark tools require Python 3.11 or later.
They validate tooling, not SGLang kernels, GPU performance, Consul integration
or Nomad scheduling. See `docs/LOCAL-VALIDATION.md` for checks actually run.

## Scope

Includes sizing experiments, intra-host TP comparison, routing, discovery,
failure recovery and internal OpenAI-compatible consumers. Excludes APISIX auth
and public exposure, fine-tuning, GPU procurement/installation and a vLLM comparison
unless SGLang fails a must-have. Native Superset is not assumed to have an OpenAI
setting: test its actual AI integration or calling service separately.
