# Evidence status

Hardware experiments have NOT been run from this workspace. No GPU, Docker,
Nomad CLI, cluster addresses, SSH session or n8n session is available here.

| Acceptance item | Status | Required evidence |
|---|---|---|
| Gateway completion via Consul discovery | Pending | passing service record, request and response |
| Both gpu-01 workers take load | Pending | gateway per-worker counter deltas, registry and worker metrics |
| AWQ / GPTQ Marlin on 12 GB | Pending | startup/kernel logs, VRAM trace, workload and measured summary |
| 8 GB placement | Pending | embeddings correctness and batching/VRAM sweep |
| Replicas vs TP=2 at C=1,8,32 | Pending | raw request JSONL and summaries, three repeats |
| Cache-aware vs round-robin | Pending | per-request cached token usage and paired throughput tables |
| Worker kill and automatic recovery | Pending | kill interval, client requests, gateway and Nomad events |
| n8n OpenAI node completion | Pending | redacted execution export or screenshot |
| Routed host link | Pending | ping and iperf3 output in both directions |
| Design reviewed with requester | Pending | review decision and required changes |

Locally tested mock responses validate tooling only. They are not GPU performance data.
