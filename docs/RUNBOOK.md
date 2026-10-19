# GPU pool deployment and validation runbook

Follow the gates in order. Record actual output; do not mark a later gate complete
because an earlier one passed. All shell commands target a Linux cluster client
or gpu-01, not a Windows PowerShell prompt. Use a test endpoint until acceptance.

## Gate 0 - environment and access

Required: authorized shell on gpu-01 and each routed host, Nomad submit/read/exec
access, Consul health read access, internal registry access and an n8n test session.
Confirm which VM owns each physical GPU through Proxmox passthrough. Nomad clients
must run where the NVIDIA devices are visible; a Proxmox host cannot allocate a
GPU already passed through to a VM. Verify IOMMU isolation with the host operator.

On gpu-01, save:

```bash
mkdir -p results/inventory
nvidia-smi -L > results/inventory/gpus.txt
nvidia-smi > results/inventory/nvidia-smi.txt
nvidia-smi topo -m > results/inventory/topology.txt
uname -a > results/inventory/kernel.txt
docker version > results/inventory/docker.txt
docker info > results/inventory/docker-info.txt
nomad version > results/inventory/nomad.txt
consul version > results/inventory/consul.txt
nomad node status -self -verbose > results/inventory/nomad-client.txt
```

Confirm the NVIDIA Container Toolkit runtime and the separately installed
Nomad NVIDIA device plugin. Verify Nomad fingerprints two healthy GPUs with
their UUIDs and memory. No `--gpus all` or hard-coded GPU IDs in Nomad jobs.
For the first by-hand test, choose an unallocated GPU UUID from `nvidia-smi -L`.

Set `meta.gpu_pools = "chat"` in the gpu-01 Nomad client configuration. Mixed-pool
clients can use `"chat,embedding,vision"`; match cards with VRAM/model constraints.
Use a separate job ID for each model pool, never overwrite the chat job to deploy
embeddings. For 8 GB devices set minimum/maximum VRAM to `7 GiB`/`9 GiB` and
verify the plan selects the intended device class. Larger cards need explicit
updated memory bounds. Add a
read-only `host_volume "sglang-models"` with `path = "/opt/sglang-models"` and
`read_only = true`. Ask the platform operator to apply client config changes;
follow the site's procedure for restarting agents without disrupting workloads.
Reserve at least 16 GB host RAM and 4,000 MHz CPU per chat allocation, then adjust
using observed CPU/RAM use. VM RAM must also accommodate shared-memory segments.

Pin a worker image release with RTX 3060/SM86 support, a compatible NVIDIA driver
and a gateway image implementing IGW and the `/workers` API. Pull selected tags,
inspect their RepoDigests, then use digests for all subsequent commands. Do not
assume that today's online documentation describes an older deployed image.

Set shell variables to confirmed values (do not paste replacement strings):

```bash
export WORKER_IMAGE='lmsysorg/sglang@sha256:YOUR_TESTED_DIGEST'
export GATEWAY_IMAGE='lmsysorg/sgl-model-gateway@sha256:YOUR_TESTED_DIGEST'
export MODEL='Qwen/Qwen2.5-7B-Instruct-AWQ'
export MODEL_REV='YOUR_HUGGING_FACE_COMMIT'
export GPU0='YOUR_FIRST_GPU_UUID'
export GPU1='YOUR_SECOND_GPU_UUID'
```

Verify image command flags **before** launching services:

```bash
docker run --rm --entrypoint python3 "$WORKER_IMAGE" -m sglang.launch_server --help > results/inventory/worker-help.txt
docker run --rm "$GATEWAY_IMAGE" --help > results/inventory/gateway-help.txt
docker image inspect "$GATEWAY_IMAGE" > results/inventory/gateway-image.json
```

Confirm `awq_marlin`, `gptq_marlin`, `triton`, `--enable-cache-report`,
`--enable-igw`, health, retry, metrics and queue flags. The job assumes the
upstream gateway Python launcher entrypoint; inspect the pinned image to confirm.
Do not fall back silently to another quantization kernel.

Pre-download the exact model revision into `/opt/sglang-models/hf` on every
eligible worker client, using an approved HF downloader environment:

```bash
HF_HOME=/opt/sglang-models/hf hf download "$MODEL" --revision "$MODEL_REV"
```

Confirm readable permissions and include tokenizer/config files. Private models
may require an HF token in the downloader; keep tokens outside this repository.
Runtime jobs are offline. A kernel compilation/cache issue must be resolved in
Day 1, before promising automatic recovery. Prewarm image/kernel caches where possible.

## Gate 1 - one GPU worker, Days 1-2

First ensure no Nomad allocation owns the selected GPU and ports 30001/30002/30000
are unused. By-hand and Nomad phases are mutually exclusive GPU users.

```bash
docker run -d --name sglang-w0 --gpus "device=$GPU0" \
  --network host --shm-size 2g \
  -v /opt/sglang-models:/models:ro \
  -e HF_HOME=/models/hf -e HF_HUB_OFFLINE=1 \
  --entrypoint python3 "$WORKER_IMAGE" \
  -m sglang.launch_server --model-path "$MODEL" --revision "$MODEL_REV" \
  --served-model-name chat-default --quantization awq_marlin --dtype float16 \
  --host 0.0.0.0 --port 30001 --tp-size 1 --context-length 4096 \
  --mem-fraction-static 0.85 --max-running-requests 16 \
  --chunked-prefill-size 1024 --attention-backend triton \
  --enable-metrics --enable-cache-report
docker logs -f sglang-w0
```

Press Ctrl+C to exit the log view; the detached worker keeps running. When initialization completes, run:

```bash
curl --fail-with-body http://127.0.0.1:30001/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"chat-default","messages":[{"role":"user","content":"Reply with: GPU pool ready"}],"max_tokens":32,"temperature":0}'
```

Save the response, startup log, `/get_server_info`, `/get_model_info` and `/metrics`.
Require finite, sensible output and log confirmation of the Marlin path. If OOM,
save it and adjust context/running requests/CUDA graph settings systematically.
Reducing static fraction may reduce KV capacity: record this tradeoff explicitly.

## Gate 2 - two workers and a gateway, Days 3-4

Repeat Gate 1's Docker command with name `sglang-w1`, `GPU1` and port `30002`.
Start the gateway with the same pinned image and policy:

```bash
docker run -d --name sglang-gateway --network host "$GATEWAY_IMAGE" \
  --host 0.0.0.0 --port 30000 \
  --enable-igw --policy cache_aware \
  --worker-urls http://127.0.0.1:30001 http://127.0.0.1:30002 \
  --balance-abs-threshold 4 --balance-rel-threshold 1.5 \
  --prometheus-host 0.0.0.0 --prometheus-port 29000 \
  --health-check-interval-secs 2 --health-check-timeout-secs 1 \
  --health-failure-threshold 1 --health-success-threshold 1 \
  --cb-failure-threshold 1 --retry-max-retries 3 --connect-timeout-secs 1 \
  --request-timeout-secs 300 --max-concurrent-requests 64 \
  --queue-size 128 --queue-timeout-secs 240
curl --fail-with-body http://127.0.0.1:30000/workers
curl --fail-with-body http://127.0.0.1:30000/v1/models
```

Send requests with model `chat-default` to gateway `/v1/chat/completions`.
Confirm the actual discovered model alias in `/workers`. Require two healthy
entries. Repeat curl through the gateway and run the C=8 workload below.

**Load evidence:** sample gateway `smg_worker_requests_active{worker="..."}` during
load and inspect `/get_loads`. Both URLs must show activity. Cross-check worker
request-counter deltas. In inspected current source, `smg_router_requests_total`
has model/endpoint labels, not worker labels, despite a documentation example
suggesting otherwise. Inspect `/metrics` on the pinned build; never invent labels.
If the chosen build lacks per-worker gauges, that observability gate is blocked
until a supported build or gateway instrumentation is selected.

## Gate 3 - reproducible experiments, Days 5-7

Install benchmark requirements in a venv. Generate the synthetic trace, then copy
`bench/manifest.example.json` to `results/environment.json` and fill observed
image digests, checkpoint commit, hardware, arguments and versions.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r bench/requirements.txt
python3 bench/make_workload.py
python3 bench/make_workload.py --unique-prefix --output bench/control-unique.jsonl
```

Example one measured run (root URLs, no trailing `/v1`):

```bash
python3 bench/benchmark.py \
  --url http://127.0.0.1:30000 --metrics-url http://127.0.0.1:29000 \
  --worker-url http://127.0.0.1:30001 --worker-url http://127.0.0.1:30002 \
  --configuration qwen25-7b-awq --manifest results/environment.json \
  --topology replicas --policy cache_aware --concurrency 8 --repeat 1 \
  --requests 256 --warmup 32 --max-tokens 128 --fixed-output --flush \
  --output results/qwen-awq-replicas-cache-c8-r1
```

`--fixed-output` uses SGLang's `ignore_eos` extension for comparable generation
lengths; verify it in the pinned release. Run a separate normal-EOS realistic test
if desired, using the same setting for both policies. Every success must include
final token usage. Missing cache usage is N/A and blocks the cache-rate deliverable.
Inspect `flush-response.txt`: HTTP 200 alone is insufficient if the gateway reports
per-worker flush failures. Stop other consumer traffic during comparisons.

| Experiment | Configurations | Concurrency | Repeats |
|---|---|---|---|
| Single-GPU sizing | AWQ/awq_marlin, GPTQ/gptq_marlin; same Qwen 7B family | 1,8,32 | 3 |
| Two GPUs | Two TP=1 replicas vs one TP=2 worker | 1,8,32 | 3 |
| Routing | Two replicas: cache_aware vs round_robin | 1,8,32 | 3 |
| Cache sensitivity | Above shared-prefix trace + redacted real trace + unique-prefix control | 1,8,32 | 3 |
| Context sensitivity | 2K, 4K, 8K actual prompt-token bands; compatible context limits | 8,32 | 3 |

Run independent cold and warmed comparisons. Cold means `--warmup 0`; warmed
means `--warmup 32`. Restart the gateway for each independent trial to reset
routing approximation state, flush every worker, then warm identically. Alternate
policy order across repeats (CA/RR, RR/CA, CA/RR). Do not benchmark the two policies
simultaneously against the same workers. Capture peak VRAM, host RAM and startup
KV-token capacity. For each run use:

```bash
nvidia-smi --query-gpu=timestamp,uuid,memory.used,memory.total,utilization.gpu,power.draw \
  --format=csv --loop-ms=500 > results/vram-RUN_NAME.csv
```

Stop that monitoring command after the run. Archive failures/OOMs too. The 32
concurrent clients can queue behind 16 active sequences per GPU; that is an
intentional queueing experiment, not a claim of 32 simultaneously resident sequences.

For GPTQ, cache `Qwen/Qwen2.5-7B-Instruct-GPTQ-Int4` at its own commit, use
`--quantization gptq_marlin`, and change the configuration label/manifest.
Add an 8B candidate only with a verified supported checkpoint, license and revision.

For TP=2, stop and remove ONLY the named lab gateway/worker containers. Launch
one worker from the Gate 1 command using `--gpus "\"device=$GPU0,$GPU1\""`, port 30001
and `--tp-size 2`; route the gateway to this single URL. Preserve dtype, checkpoint,
context and output settings. Use max-running-requests 32 for equal total sequence
slots to the two 16-slot replicas, and record the different total KV capacity.
Check PCIe topology/P2P and save `nvidia-smi topo -m`; no NVLink is assumed.

For 8 GB: test `BAAI/bge-m3` FP16 with `--is-embedding`, Triton attention,
`--context-length 2048`, `--max-running-requests 4` and `--mem-fraction-static 0.8`.
Omit `--quantization` and give it alias `embed-default`. Sweep input length
512/2048 and batch size 1/4/8; then test 8192 only if measured headroom permits.
Use `/v1/embeddings` with `{"model":"embed-default","input":["test passage"]}`.
Require finite vectors, expected 1024 dimensions, consistent repeat output, and
retrieval quality on known FAQ examples. This endpoint gives dense embeddings;
do not assume all sparse/ColBERT modes are exposed. A smaller 1-3B chat model is
a fallback candidate, not an already validated 8 GB placement.

Generate the results after actual runs:

```bash
python3 bench/report.py --results results --output results/benchmark-tables.md
```

Keep all raw run directories. Report median results across three repeats and
their range, together with failure counts and actual prompt/output distributions.
The script lists each run, paired throughput deltas, and run-level medians/ranges.
Do not compare configurations with different manifests as if identical. Use the
workload generator's section/passage options to vary input length, and use recorded
prompt token counts to identify the actual 2K/4K/8K bands.

## Gate 4 - Nomad and discovery, Days 8-10

Stop/remove only the named by-hand containers to free both GPUs. Create the
discovery image with a pinned Python base and push to your internal registry:

```bash
docker build --build-arg PYTHON_IMAGE='python:3.12-slim@sha256:YOUR_DIGEST' \
  -t YOUR_INTERNAL_REGISTRY/sglang-discovery:lab discovery
docker push YOUR_INTERNAL_REGISTRY/sglang-discovery:lab
```

Copy worker and gateway var-file examples; fill your datacenter, reachable
Consul agent, image digests and model revision. Store a Consul service-read token
as `consul_token` at Nomad Variable path `nomad/jobs/sglang-gateway` using your
approved secret-handling process. Ensure workload identity may read that path.
Never place the token in a committed var file. Consul credentials used by Nomad
service registration are configured on agents separately.

```bash
nomad job validate -var-file=nomad/lab.vars.hcl nomad/workers.nomad.hcl
nomad job plan -var-file=nomad/lab.vars.hcl nomad/workers.nomad.hcl
nomad job run -var-file=nomad/lab.vars.hcl nomad/workers.nomad.hcl
nomad job validate -var-file=nomad/gateway.vars.hcl nomad/gateway.nomad.hcl
nomad job plan -var-file=nomad/gateway.vars.hcl nomad/gateway.nomad.hcl
nomad job run -var-file=nomad/gateway.vars.hcl nomad/gateway.nomad.hcl
```

Do not submit until the plan places exactly two one-GPU allocations on gpu-01
for the initial test. GPU memory constraints admit cards in the 11-13 GiB class; constrain
`node.unique.name` to the confirmed gpu-01 Nomad name for this test if other pool
clients exist. Remove that temporary node constraint when scaling across hosts.
`replicas * gpu_count` must equal GPUs assigned to the pool; `gpu_count == tp_size`.
Nomad's group count is not a GPU-inventory expression. Memory bounds constrain
the device class; pool labels constrain eligible clients rather than individual
physical cards. On mixed clients inspect every plan to avoid starving another pool.

Discover the gateway via Consul's passing health API. Its dynamic port is in the
service record, so a bare `sglang-gateway.service.consul:30000` assumption is wrong.
Use an SRV-capable client or derive `http://Service.Address:Service.Port` from
`/v1/health/service/sglang-gateway?passing=true`. Fall back to Node.Address only
when Service.Address is blank. For n8n's fixed Base URL, use an internal stable
DNS/VIP/port provided by the platform team, or deliberately reserve a fixed
gateway port and enforce address stability; register that gateway in Consul too.
APISIX is deferred. A dynamic gateway allocation URL works for the acceptance
test but is not stable across gateway rescheduling.

The controller polls passing workers every 2 seconds, adds them through
`POST /workers`, and removes absent workers after two successful snapshots using
`DELETE /workers/{id}`. It preserves the registry on Consul failures. Gateway
health/circuit breakers handle the faster failure path. Watch discovery logs:
asynchronous registration is complete only after the worker is listed healthy.
If schema/alias mismatches occur, stop and adapt to the pinned gateway API.

Verify recovery from worker address change, gateway restart and controller
restart. Simulate a Consul read outage in the lab; existing healthy routing must
continue and controller logs must show preservation of the registry.

## Gate 5 - worker kill

Run against two Nomad-managed replicas at C=8 first. Confirm a baseline with no
client failures and sufficient surviving-worker capacity. Select the worker
allocation ID from `nomad job status sglang-chat`. Run:

```bash
python3 bench/failure.py --url http://GATEWAY_ADDRESS:GATEWAY_PORT \
  --metrics-url http://GATEWAY_ADDRESS:METRICS_PORT \
  --allocation CONFIRMED_WORKER_ALLOCATION_ID \
  --duration 180 --kill-after 20 --concurrency 8 \
  --output results/failure-c8
```

This deliberately SIGKILLs the worker container's PID 1 through Nomad exec.
It does not stop the job or change desired count. Streaming calls already served
by the killed worker may fail; they cannot be transparently resumed after bytes
have been emitted. No client-side retry is performed.

Review the kill-command time interval against task events. Require zero baseline
failures and zero failures for requests starting after the kill, including the
health-detection interval. The script flags the command-time ambiguity instead
of automatically excusing it. Confirm the task died, restarted (or rescheduled),
is healthy, reappeared in the gateway, and both workers resumed load. Save logs
with UTC timestamps. Repeat at C=32 and verify overload does not introduce 429,
408 or timeout failures. If it does, the must-have is not met: tune admission,
retry/queue limits or capacity and rerun. Do not promise zero loss from health
checks alone. This test excludes simultaneous host or gateway failure.

## Gate 6 - n8n and routed link

In the **OpenAI node**, create a test credential with the internal gateway Base
URL ending in `/v1`, and use the exact model alias from `/v1/models` (normally
`chat-default`). Use a dummy credential string only for the deliberately unauthenticated
private test gateway if the node requires it. Choose a chat-completion operation,
not an Assistants or Responses operation unless separately validated. Send
`Reply with GPU pool ready`. Save a redacted successful execution and response.
UI names differ by n8n release; verify the actual node supports a custom Base URL.
If it does not, resolve that specific integration; an HTTP Request node test does
not satisfy the required OpenAI-node acceptance.

On G6326A start an approved private iperf3 listener: `iperf3 -s -1` handles ONE
test; use `iperf3 -s` for the three measurements below and stop it afterwards.
On G6326B run:

```bash
bash bench/link-test.sh ROUTED_G6326A_IP results/link-b-to-a
```

Restrict TCP 5201 to the peer host and run during an agreed test window. Repeat
from the actual VM interfaces used by workers if they differ from host routing.
Read median/min/max RTT and achieved receiver bandwidth, not nominal NIC speed.
Cross-host TP remains excluded. Reconsider it only after actual NCCL collective
benchmarks on that path meet an agreed latency/throughput budget.

## Gate 7 - review, Days 11-12

Update the three-page design with measured winners, concurrency/context limits,
placement inventory and measured link paragraph. Attach raw benchmark runs,
failure evidence and n8n completion. Review with the requester and record the
decision. Every item in `results/STATUS.md` must be completed or explicitly
rejected before calling the task done.
