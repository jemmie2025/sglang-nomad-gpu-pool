# Proxmox GPU Pool — SGLang, Nomad & Consul

Deployment configuration and validation tooling for an internal OpenAI-compatible inference service, starting with two RTX 3060 12 GB GPUs on `gpu-01` and planned expansion to 10–12 GPUs.

**Status:** 14 local tooling tests passed. GPU deployment, performance benchmarks, failure recovery and n8n integration remain pending.

## Architecture

**Applications / n8n → SGLang Model Gateway → SGLang workers**

- **SGLang:** one worker per GPU, with cache-aware gateway routing.
- **Nomad:** GPU allocation, scheduling and worker recovery.
- **Consul:** service registration and health-based discovery.
- **Discovery controller:** synchronize healthy workers with the gateway.

Separate pools are planned for chat, embedding and vision workloads. The initial single gateway remains a single point of failure.

## Proposed Configuration

| Setting | Initial value |
|---|---|
| Host | `gpu-01`, G6326B |
| Hardware | 2 × RTX 3060 12 GB |
| Workers | 2 replicas, 1 GPU each |
| Model candidate | Qwen2.5-7B-Instruct-AWQ |
| Execution | TP=1, Marlin quantization backend |
| KV cache / attention | FP16 / Triton |
| Context | 4,096 tokens initially |
| Deployment | Pinned images and pre-cached model revisions |

Model compatibility, memory limits and concurrency require hardware validation. Nomad replica count must match eligible GPUs within each pool.

## Challenges and Validation

| Challenge | Validation |
|---|---|
| GPU memory limits | Test model fit and concurrency on 12 GB and 8 GB GPUs |
| Scaling efficiency | Compare two replicas with intra-host TP=2 at concurrency 1, 8 and 32 |
| Routing performance | Compare cache-aware routing with round-robin using shared prompts and RAG |
| Discovery and recovery | Compare discovery approaches; test worker removal and automatic recovery |
| Multi-host expansion | Measure network latency and bandwidth; validate model-pool routing |

## Milestones and Acceptance

1. Verify GPU passthrough, drivers, containers and network access.
2. Run one worker, then two workers behind the gateway.
3. Record latency, throughput, memory usage, errors and available cache metrics.
4. Deploy through Nomad and validate discovery and worker recovery.
5. Complete an n8n inference request and document the 10–12 GPU placement plan.

Acceptance requires evidence that both workers serve requests, recovery succeeds, n8n completes a request, and measured benchmark tables are populated.

## Repository

- `nomad/` — deployment jobs and example configuration.
- `discovery/` — worker discovery controller.
- `bench/` and `tests/` — benchmark tools and local tests.
- `docs/RUNBOOK.md` — deployment and validation instructions.
- `docs/design.pdf` — architecture and placement plan.
- `results/STATUS.md` — evidence and outstanding acceptance checks.

## Local Tests

Requires `uv` installed and available on your shell path:

    uv venv --python 3.12 .venv
    uv pip install --python .venv/bin/python -r bench/requirements.txt
    .venv/bin/python -m unittest discover -s tests -v

Local tests validate tooling using mock endpoints; they do not prove GPU performance or cluster readiness.

## Access and Scope

Required: approved host access, GPU-container permissions, Nomad/Consul permissions and network support. n8n access is available.

APISIX authentication/public exposure, fine-tuning and GPU installation are excluded. Cross-host tensor parallelism is excluded from the baseline unless measurements justify it.