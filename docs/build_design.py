"""Rebuild the three-page design PDF and its matching Markdown source."""

from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

HERE = Path(__file__).resolve().parent
PAGES = [
    (
        "Architecture and GPU sizing",
        [
            (
                "Decision and status",
                "Use independent TP=1 workers as the default: one Nomad allocation reserves one GPU. An SGLang Model Gateway exposes the OpenAI-compatible API and routes by model alias. Register the gateway and workers in Consul. APISIX remains deferred. This is a design for validation, not a completed deployment; hardware results and requester review are pending.",
            ),
            (
                "Serving path",
                "Internal consumers -> gateway -> model-specific worker pool -> GPU. Chat consumers share alias chat-default; embeddings use embed-default and vision uses vision-default. Current IGW mode supports model-specific routing and worker registration [1]. Prove model isolation with positive and unknown-model tests on the pinned release. Never route a chat request to an embedding worker. Begin with one gateway; multiple gateways require an explicit HA and cache-locality decision.",
            ),
            (
                "12 GB chat candidate",
                "First test Qwen/Qwen2.5-7B-Instruct-AWQ with awq_marlin, then the same family's GPTQ-Int4 checkpoint with gptq_marlin [2,3]. Use FP16 activations/KV, TP=1, Triton attention, a 4,096-token total context limit and static memory fraction 0.85. These are starting settings, not measured limits. Verify SM86 compatibility and actual Marlin selection from startup logs. Keep the model revision and image digest fixed in each experiment.",
            ),
            (
                "KV-cache budget",
                "For Qwen's published configuration, FP16 KV bytes per token = 2 x 28 layers x 4 KV heads x 128 head dimension x 2 bytes = 57,344 bytes (56 KiB) [3]. A 4,096-token sequence consumes about 224 MiB before runtime overhead. An illustrative 3-5 GiB KV budget holds roughly 13-22 such sequences without prefix sharing. Actual available KV depends on checkpoint tensors, CUDA graphs, workspaces and allocator overhead; read startup capacity and measure peak VRAM. A 16-active-request cap is an experiment setting. Begin production conservatively until long-prompt tests pass.",
            ),
            (
                "8 GB and vision candidates",
                "Prefer BAAI/bge-m3 FP16 on 8 GB cards; verify dense /v1/embeddings, 1,024 dimensions, retrieval quality and batch/input-length memory limits [4]. Start at 2K context and small batches, not the model's longest advertised context. Encoder embeddings do not use a decoder KV budget like chat. For vision, test Qwen2.5-VL-3B-Instruct-AWQ on a 12 GB card with image-resolution and image-token limits. Both placements remain candidates until their pinned SGLang builds pass.",
            ),
        ],
        None,
    ),
    (
        "Discovery, routing and recovery",
        [
            (
                "Discovery choice",
                "Recommend a small controller beside each gateway: query Consul passing worker services, reconcile their addresses/model tags against GET /workers, and use POST /workers plus DELETE /workers/{id}. Registration is asynchronous; a successful POST is not readiness [1]. This makes worker restarts, reschedules, controller restarts and gateway restarts converge without changing gateway startup arguments. Nomad remains the owner of process lifecycle; Consul supplies membership; the gateway owns health and routing.",
            ),
            (
                "Alternatives and operational safeguards",
                "A Consul-template worker list is simple but requires an explicit gateway reload mechanism; restarting the gateway on every address change risks interrupting streams and loses routing state. Native discovery in the checked documentation targets Kubernetes, not Nomad/Consul. The supplied controller polls every two seconds, removes workers after two successful absent snapshots, preserves registry state on read failures or ACL-filtered results, and rate-limits duplicate asynchronous adds. One controller owns the complete worker registry of its gateway. Keep API access internal and use a least-privilege Consul read token.",
            ),
            (
                "Replicas versus intra-host TP=2",
                "Benchmark two TP=1 replicas against one TP=2 worker on gpu-01 using the same model, dtype, context, requests and output cap. Test concurrency 1, 8 and 32. Use equal total active-sequence caps: 16 per replica versus 32 for TP=2; report differing measured KV capacity. TP may lower single-request latency or admit larger contexts, but PCIe collectives and virtualization can reduce aggregate throughput. Replicas provide independent failure domains. Choose the measured winner for the target workload, not an assumed result.",
            ),
            (
                "Cache-aware routing experiment",
                "Replay a long shared system prompt followed by repeated department RAG passages and a unique question suffix. Compare round_robin and cache_aware with identical traces; add a unique-prefix control and a redacted real trace. Restart gateway routing state, flush all workers and run separate cold/warmed trials. Alternate policy order and repeat three times. Report request/s, output token/s, TTFT p50/p95, end-to-end p50/p95, failures and token-weighted cache reuse. Cache reuse = sum(cached prompt tokens) / sum(prompt tokens); missing usage is unknown, not zero. Shared text must precede unique text to reuse prefixes.",
            ),
            (
                "Worker failure requirement",
                "Kill worker container PID 1 during sustained C=8 and C=32 load. Nomad restart/reschedule and the controller must restore two healthy workers without manual repair. Gateway probes every two seconds with a one-failure threshold; retries and circuit breakers must cover the remaining detection window. Require no new-request failures after the kill and preserve raw evidence. A stream already emitting from the dead worker may fail and cannot be resumed transparently. Capacity after losing one GPU and queue timeouts must be tested. Do not extend this result to whole-host or gateway failure.",
            ),
        ],
        None,
    ),
    (
        "Fleet placement and acceptance",
        [
            (
                "Placement proposal",
                "The card mix is not yet supplied. The counts below assume enough validated 12 GB cards for chat/vision and 8 GB-capable cards for embeddings. They allocate the entire fleet, including one spare. If the actual mix differs, revise the table before deployment; never force an oversized model onto a smaller card.",
            ),
            (
                "Scheduling and endpoints",
                "Set each pool's group count explicitly to its assigned GPU count; device.count=1 prevents concurrent allocations owning the same reserved device [5]. Count does not auto-expand when a GPU is installed. Client metadata identifies eligible pools; VRAM/model constraints select device classes. Use separate job IDs per model pool. Keep images and model snapshots cached on every eligible client. Consul SRV or the health API supplies dynamic gateway ports. n8n needs a stable internal DNS/VIP or an explicitly stable endpoint ending in /v1; its test allocation URL alone is not a durable endpoint.",
            ),
            (
                "Cross-host TP: excluded pending measurements",
                "Multi-node tensor parallelism exchanges intermediate activations through collectives across many model layers and decode steps. A routed link without a high-speed GPU fabric adds bandwidth limits, per-collective latency and possible host staging. For this Qwen model, one FP16 hidden-state tensor is 7,168 bytes per token: a 2,048-token prefill is about 14 MiB before collective multiplication. The supplied test records 50 pings and 20-second forward/reverse iperf3 runs on the actual worker path. Measured RTT and bandwidth are PENDING; no numbers are inferred from NIC labels. Those tests characterize the path but do not measure NCCL itself. Keep cross-host TP excluded unless subsequent NCCL and serving benchmarks meet an agreed budget; replicas can cross hosts without per-token collectives.",
            ),
            (
                "Risks and release gates",
                "Main risks: Ampere/kernel compatibility, insufficient KV at long contexts, vision activation peaks, PCIe/P2P limitations, CPU/RAM or thermal bottlenecks, cold restart times, stale membership, single-gateway failure and n8n API-operation mismatches. Control them with pinned builds, load/VRAM monitoring, offline model caches, bounded queues and the staged acceptance tests. The Superset consumer needs its actual AI integration validated; native OpenAI configuration is not assumed. Tool calling and application quality require their own smoke tests if used by agents.",
            ),
            (
                "Acceptance and review",
                "Complete only after: a Consul-discovered gateway serves chat; gateway per-worker load metrics show both workers active; the kill test meets the client-loss rule and Nomad recovers; the actual n8n OpenAI node completes; replicas/TP and routing tables contain measured results; and this design is reviewed with the requester. The supplied pack prepares these tests. All hardware evidence remains pending. The 12-day sequence is a proposed schedule, not an elapsed-time guarantee.",
            ),
        ],
        [
            ["Pool", "10 cards", "12 cards", "Placement"],
            ["Chat", "7", "8", "12 GB; validated 7B INT4"],
            ["Embeddings", "1", "2", "8 GB preferred; bge-m3"],
            ["Vision", "1", "1", "12 GB; bounded 3B VLM"],
            ["Spare / validation", "1", "1", "Match a critical pool's card class"],
            ["Total", "10", "12", "Scale after measured demand"],
        ],
    ),
]
REFERENCES = [
    (
        "[1] SGLang gateway: IGW, worker lifecycle and reliability",
        "https://docs.sglang.io/docs/advanced_features/sgl_model_gateway",
    ),
    (
        "[2] SGLang quantization: CUDA Marlin methods",
        "https://docs.sglang.io/docs/advanced_features/quantization",
    ),
    (
        "[3] Qwen 7B AWQ configuration",
        "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct-AWQ/blob/main/config.json",
    ),
    ("[4] BGE-M3 model card", "https://huggingface.co/BAAI/bge-m3"),
    (
        "[5] Nomad GPU device specification",
        "https://developer.hashicorp.com/nomad/docs/job-specification/device",
    ),
]


def build():
    pdfmetrics.registerFont(
        TTFont("PoolSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    )
    pdfmetrics.registerFont(
        TTFont("PoolSans-Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    )
    body = ParagraphStyle(
        "body",
        fontName="PoolSans",
        fontSize=9.4,
        leading=12.6,
        textColor=colors.HexColor("#26374B"),
        spaceAfter=6,
    )
    heading = ParagraphStyle(
        "heading",
        parent=body,
        fontName="PoolSans-Bold",
        fontSize=10.2,
        leading=13,
        textColor=colors.HexColor("#087E8B"),
        spaceBefore=5,
        spaceAfter=3,
    )
    title = ParagraphStyle(
        "title",
        parent=body,
        fontName="PoolSans-Bold",
        fontSize=22,
        leading=26,
        textColor=colors.HexColor("#11263F"),
        spaceAfter=8,
    )
    small = ParagraphStyle(
        "small", parent=body, fontSize=7.5, leading=9.5, spaceAfter=2
    )
    markdown = [
        "# Proxmox GPU pool design",
        "",
        "13 October 2026 | Draft for hardware validation and review",
        "",
    ]
    story = []
    for i, (page_title, sections, rows) in enumerate(PAGES):
        if i:
            story.append(PageBreak())
        story.append(Paragraph(page_title, title))
        story.append(
            Paragraph("PROXMOX / NOMAD / CONSUL / SGLANG  |  VALIDATION DRAFT", small)
        )
        story.append(Spacer(1, 7))
        markdown += [f"## Page {i + 1}: {page_title}", ""]
        for j, (subhead, text) in enumerate(sections):
            story.append(Paragraph(escape(subhead), heading))
            story.append(Paragraph(escape(text), body))
            markdown += [f"### {subhead}", "", text, ""]
            if rows and j == 0:
                cells = [
                    [Paragraph(escape(cell), small) for cell in row] for row in rows
                ]
                table = Table(
                    cells, colWidths=[105, 52, 52, 282], repeatRows=1, hAlign=TA_LEFT
                )
                table.setStyle(
                    TableStyle(
                        [
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E3F1F4")),
                            (
                                "ROWBACKGROUNDS",
                                (0, 1),
                                (-1, -1),
                                [colors.white, colors.HexColor("#F5F7FA")],
                            ),
                            ("VALIGN", (0, 0), (-1, -1), "TOP"),
                            ("LEFTPADDING", (0, 0), (-1, -1), 7),
                            ("TOPPADDING", (0, 0), (-1, -1), 4),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                        ]
                    )
                )
                story += [table, Spacer(1, 4)]
                markdown += ["| " + " | ".join(rows[0]) + " |", "|---|---:|---:|---|"]
                markdown += ["| " + " | ".join(row) + " |" for row in rows[1:]] + [""]
        if i == 2:
            story.append(
                Paragraph("Primary references (checked 13 October 2026)", heading)
            )
            for label, url in REFERENCES:
                story.append(
                    Paragraph(
                        f'<link href="{escape(url)}" color="#087E8B">{escape(label)}</link>',
                        small,
                    )
                )

    markdown += ["## References", ""] + [
        f"- [{label}]({url})" for label, url in REFERENCES
    ]
    (HERE / "design.md").write_text("\n".join(markdown) + "\n")

    def footer(canvas, doc):
        width, _ = A4
        canvas.setStrokeColor(colors.HexColor("#D5DEE8"))
        canvas.line(48, 40, width - 48, 40)
        canvas.setFont("PoolSans", 8)
        canvas.setFillColor(colors.HexColor("#54677D"))
        canvas.drawString(
            48,
            26,
            "GPU pool architecture | 13 October 2026 | Hardware validation pending",
        )
        canvas.drawRightString(width - 48, 26, str(doc.page))

    doc = SimpleDocTemplate(
        str(HERE / "design.pdf"),
        pagesize=A4,
        title="Proxmox GPU Pool - Architecture Validation",
        author="Architecture validation project",
        subject="Three-page design draft; hardware acceptance pending",
        rightMargin=48,
        leftMargin=48,
        topMargin=42,
        bottomMargin=52,
    )
    doc.build(story, onFirstPage=footer, onLaterPages=footer)


if __name__ == "__main__":
    build()
