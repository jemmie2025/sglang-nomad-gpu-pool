# Local verification performed

Prepared 13 October 2026. These checks validate the supplied artifacts, not the cluster.

| Check | Actual outcome | Limit |
|---|---|---|
| Nomad 1.10.5 `job validate`, worker job | Passed | Offline; Docker driver, GPU placement and agent configuration not validated |
| Nomad 1.10.5 `job validate`, gateway job | Passed | Offline; Consul registration, image entrypoint and secrets not exercised |
| Ruff lint | Passed | Static source checks only |
| Python test suite | 14 passed | Local mock streaming server and discovery fixtures |
| Link script shell syntax | Passed | No host-to-host measurement performed |
| Design PDF | 3 A4 pages; visually reviewed | Design draft; measurements/review pending |

The test suite covers Consul address fallback and IPv6, model tags, asynchronous
registration cooldown, two-snapshot removal, reappearance, alias mismatch,
token-weighted cache calculations, missing cache data, truncated streams, missing
usage, full benchmark output capture and report generation.

Tests used Python 3.12 and aiohttp 3.14.4. Mock timing outputs were transient and
are deliberately excluded from the hardware results directory. No measured GPU
numbers have been substituted with mock results.

Image CLI flags were checked against current primary documentation/source,
not by running container images in this workspace. Pin and inspect the selected
build on gpu-01 before deployment. The offline Nomad CLI explicitly reported:
`Driver configuration not validated since connection to Nomad agent couldn't be established.`

Hardware acceptance remains pending in `results/STATUS.md`. The design review
with the requester also remains pending.
