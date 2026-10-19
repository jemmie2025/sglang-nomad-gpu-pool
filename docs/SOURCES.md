# Primary sources checked 13 October 2026

These are current documentation/source references, not proof that an unspecified
older image supports the same flags. Pin the deployed version and archive its help.

1. [SGLang Model Gateway](https://docs.sglang.io/docs/advanced_features/sgl_model_gateway) - IGW, worker lifecycle API, routing, retries and readiness.
2. [Gateway README](https://github.com/sgl-project/sglang/blob/main/sgl-model-gateway/README.md) - worker registration schema and model-specific routing.
3. [Gateway metrics source](https://github.com/sgl-project/sglang/blob/main/sgl-model-gateway/src/observability/metrics.rs) - per-worker active-request gauge and actual counter labels.
4. [Gateway Dockerfile](https://github.com/sgl-project/sglang/blob/main/docker/gateway.Dockerfile) - upstream image entrypoint.
5. [SGLang server arguments](https://docs.sglang.io/docs/advanced_features/server_arguments) - dtype, context, quantization, concurrency, cache and attention flags.
6. [SGLang quantization](https://docs.sglang.io/docs/advanced_features/quantization) - NVIDIA Marlin AWQ/GPTQ support; distinguish offline checkpoints from online quantization.
7. [Qwen AWQ configuration](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-AWQ/blob/main/config.json) - 28 layers, 4 KV heads, head dimension 128, 4-bit AWQ.
8. [Qwen GPTQ checkpoint](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-GPTQ-Int4/tree/main) - second candidate for the controlled quantization comparison.
9. [BGE-M3 model card](https://huggingface.co/BAAI/bge-m3) - embedding capabilities and dimensions; model support still requires release-specific testing.
10. [Qwen 3B vision AWQ model card](https://huggingface.co/Qwen/Qwen2.5-VL-3B-Instruct-AWQ) - vision candidate, not a measured placement.
11. [Nomad device job specification](https://developer.hashicorp.com/nomad/docs/job-specification/device) - GPU count, memory constraint and device isolation.
12. [Nomad NVIDIA device plugin](https://developer.hashicorp.com/nomad/plugins/devices/nvidia) - plugin and Docker runtime prerequisites.
13. [Consul health API](https://developer.hashicorp.com/consul/api-docs/health) - passing service instances and service/node addresses.
14. [SGLang production metrics](https://docs.sglang.io/docs/references/production_metrics) - worker metrics; cache-hit gauge is distinct from request-weighted token reuse.
15. [NVIDIA vendor usage reference](https://docs.nvidia.com/aiperf/reference/vendor-usage-field-reference) - SGLang cached-token usage requires cache reporting.

Sizing budgets, replica preference, initial pool allocations, threshold settings,
and the cross-host TP exclusion are design proposals/inferences to validate on this
fleet. They are not copied performance claims from other GPU hardware.
