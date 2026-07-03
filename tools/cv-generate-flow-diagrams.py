#!/usr/bin/env python3
"""Generate O2T architecture diagrams as draw.io XML and SVG."""

from __future__ import annotations

import html
import subprocess
import textwrap
import zlib
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "diagrams"


@dataclass(frozen=True)
class Box:
    id: str
    label: str
    x: int
    y: int
    w: int
    h: int
    fill: str
    stroke: str = "#384152"


@dataclass(frozen=True)
class Edge:
    src: str
    dst: str
    label: str = ""


@dataclass(frozen=True)
class Diagram:
    name: str
    title: str
    width: int
    height: int
    boxes: tuple[Box, ...]
    edges: tuple[Edge, ...]


def lines(label: str, width: int) -> list[str]:
    result: list[str] = []
    for raw in label.split("\n"):
        wrapped = textwrap.wrap(raw, width=width, break_long_words=False) or [""]
        result.extend(wrapped)
    return result


def svg_for(diagram: Diagram) -> str:
    boxes = {box.id: box for box in diagram.boxes}
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{diagram.width}" height="{diagram.height}" viewBox="0 0 {diagram.width} {diagram.height}">',
        "<defs>",
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">',
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#384152"/>',
        "</marker>",
        "</defs>",
        '<rect width="100%" height="100%" fill="#f8fafc"/>',
        f'<text x="36" y="44" fill="#111827" font-family="Inter, Arial, sans-serif" font-size="26" font-weight="700">{html.escape(diagram.title)}</text>',
    ]
    for edge in diagram.edges:
        src = boxes[edge.src]
        dst = boxes[edge.dst]
        x1, y1 = src.x + src.w, src.y + src.h // 2
        x2, y2 = dst.x, dst.y + dst.h // 2
        if dst.x < src.x:
            x1, y1 = src.x + src.w // 2, src.y + src.h
            x2, y2 = dst.x + dst.w // 2, dst.y
        parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#384152" stroke-width="2.2" marker-end="url(#arrow)"/>'
        )
        if edge.label:
            lx, ly = (x1 + x2) // 2, (y1 + y2) // 2 - 8
            parts.append(
                f'<text x="{lx}" y="{ly}" text-anchor="middle" fill="#475569" font-family="Inter, Arial, sans-serif" font-size="13">{html.escape(edge.label)}</text>'
            )
    for box in diagram.boxes:
        parts.append(
            f'<rect x="{box.x}" y="{box.y}" width="{box.w}" height="{box.h}" rx="8" fill="{box.fill}" stroke="{box.stroke}" stroke-width="1.6"/>'
        )
        wrapped = lines(box.label, max(12, box.w // 9))
        start_y = box.y + box.h // 2 - (len(wrapped) - 1) * 9
        for index, line in enumerate(wrapped):
            weight = "700" if index == 0 else "400"
            size = 15 if index == 0 else 13
            parts.append(
                f'<text x="{box.x + box.w / 2:.1f}" y="{start_y + index * 18}" text-anchor="middle" dominant-baseline="middle" fill="#111827" font-family="Inter, Arial, sans-serif" font-size="{size}" font-weight="{weight}">{html.escape(line)}</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)


def drawio_for(diagram: Diagram) -> str:
    mxfile = ET.Element("mxfile", {"host": "app.diagrams.net"})
    root = ET.SubElement(mxfile, "diagram", {"name": diagram.title, "id": diagram.name})
    model = ET.SubElement(
        root,
        "mxGraphModel",
        {
            "dx": str(diagram.width),
            "dy": str(diagram.height),
            "grid": "1",
            "gridSize": "10",
            "guides": "1",
            "tooltips": "1",
            "connect": "1",
            "arrows": "1",
            "fold": "1",
            "page": "1",
            "pageScale": "1",
            "pageWidth": str(diagram.width),
            "pageHeight": str(diagram.height),
            "math": "0",
            "shadow": "0",
        },
    )
    graph = ET.SubElement(model, "root")
    ET.SubElement(graph, "mxCell", {"id": "0"})
    ET.SubElement(graph, "mxCell", {"id": "1", "parent": "0"})
    ET.SubElement(
        graph,
        "mxCell",
        {
            "id": "title",
            "value": html.escape(diagram.title),
            "style": "text;html=1;strokeColor=none;fillColor=none;fontSize=26;fontStyle=1;align=left;verticalAlign=middle;",
            "vertex": "1",
            "parent": "1",
        },
    ).append(ET.Element("mxGeometry", {"x": "36", "y": "18", "width": "720", "height": "36", "as": "geometry"}))
    for box in diagram.boxes:
        cell = ET.SubElement(
            graph,
            "mxCell",
            {
                "id": box.id,
                "value": html.escape(box.label).replace("\n", "<br>"),
                "style": f"rounded=1;whiteSpace=wrap;html=1;fillColor={box.fill};strokeColor={box.stroke};fontSize=13;fontColor=#111827;",
                "vertex": "1",
                "parent": "1",
            },
        )
        cell.append(
            ET.Element(
                "mxGeometry",
                {"x": str(box.x), "y": str(box.y), "width": str(box.w), "height": str(box.h), "as": "geometry"},
            )
        )
    for index, edge in enumerate(diagram.edges, start=1):
        cell = ET.SubElement(
            graph,
            "mxCell",
            {
                "id": f"edge{index}",
                "value": html.escape(edge.label),
                "style": "edgeStyle=orthogonalEdgeStyle;rounded=0;orthogonalLoop=1;jettySize=auto;html=1;endArrow=block;strokeColor=#384152;fontSize=12;fontColor=#475569;",
                "edge": "1",
                "parent": "1",
                "source": edge.src,
                "target": edge.dst,
            },
        )
        cell.append(ET.Element("mxGeometry", {"relative": "1", "as": "geometry"}))
    return ET.tostring(mxfile, encoding="unicode", xml_declaration=True)


DIAGRAMS = (
    Diagram(
        name="o2t-flow",
        title="O2T End-to-End Flow",
        width=1320,
        height=620,
        boxes=(
            Box("inputs", "Inputs\nLLVM pass source, compile_commands.json, constraints, seeds", 42, 105, 235, 110, "#dbeafe"),
            Box("source", "Source Mining\ncv-mine-pass-source-ast\nmarkers, predicates, rewrites, guards", 345, 80, 245, 130, "#e0f2fe"),
            Box("impl", "Implementation IR Mining\ncv-mine-pass-impl-ir\nCFG, DFG, calls, operands", 345, 250, 245, 130, "#e0f2fe"),
            Box("intent", "Intent Inference\ncv-infer-optimization-intent.py\nformal before/after model", 655, 145, 245, 130, "#dcfce7"),
            Box("proof", "Validation\nSMT/Alive2-compatible checks\nproved, failed, unsupported", 965, 80, 245, 130, "#fef9c3"),
            Box("audit", "Audit Reports\nfindings.json, run-summary.json, real-pass-readiness", 965, 250, 245, 130, "#fce7f3"),
            Box("gate", "Production Gate\nbudgets, baselines, regressions", 965, 430, 245, 95, "#ede9fe"),
        ),
        edges=(
            Edge("inputs", "source", "pass source"),
            Edge("inputs", "impl", "compiled pass"),
            Edge("source", "intent", "source_intent_graph"),
            Edge("impl", "intent", "IR evidence"),
            Edge("intent", "proof", "formal obligations"),
            Edge("intent", "audit", "candidate evidence"),
            Edge("audit", "gate", "coverage + deltas"),
        ),
    ),
    Diagram(
        name="o2t-tooling-map",
        title="O2T Tooling Map",
        width=1320,
        height=650,
        boxes=(
            Box("campaign", "Generation Campaign\ncv-run-campaign.py\ncv-replay", 42, 95, 230, 110, "#dbeafe"),
            Box("klee", "Exploration\nKLEE harnesses\nktest extraction", 42, 265, 230, 110, "#dbeafe"),
            Box("sourceaudit", "Pass Source Audit\ncv-run-pass-source-audit.py\norchestrates miners + validation", 360, 95, 275, 130, "#dcfce7"),
            Box("external", "External Pass Audit\ncv-run-external-pass-audit.py\nthird-party wrapper", 360, 285, 275, 110, "#dcfce7"),
            Box("miners", "Native Miners\ncv-mine-pass-source-ast\ncv-mine-pass-impl-ir", 720, 90, 250, 130, "#e0f2fe"),
            Box("intenttools", "Intent Tools\ninfer, validate, promote,\ncoverage audit", 720, 285, 250, 130, "#fef9c3"),
            Box("artifacts", "Artifacts\n.ll cases, intent JSONL,\ncoverage, readiness, baselines", 1040, 185, 235, 140, "#fce7f3"),
            Box("ledger", "Progress Ledger\ndocs/llvm_transform_verification_ledger.md", 1040, 420, 235, 90, "#ede9fe"),
        ),
        edges=(
            Edge("campaign", "artifacts", "generated IR"),
            Edge("klee", "campaign", "seed configs"),
            Edge("sourceaudit", "miners", "source + compile db"),
            Edge("external", "sourceaudit", "delegates"),
            Edge("miners", "intenttools", "graphs + evidence"),
            Edge("intenttools", "artifacts", "validated records"),
            Edge("artifacts", "ledger", "capability status"),
        ),
    ),
    Diagram(
        name="o2t-verification-loop",
        title="O2T Verification Loop",
        width=1320,
        height=620,
        boxes=(
            Box("detect", "Detect Optimization Site\nmarker, matcher, rewrite region", 62, 110, 235, 110, "#dbeafe"),
            Box("model", "Model Intent\nsource-derived before/after,\nguards, replacement binding", 365, 85, 245, 135, "#dcfce7"),
            Box("mineir", "Mine Pass Implementation\ncalls, operands, SSA/call edges", 365, 275, 245, 115, "#e0f2fe"),
            Box("check", "Check Consistency\nsource graph vs intent\nimpl-IR evidence vs rewrite", 680, 155, 255, 140, "#fef9c3"),
            Box("prove", "Prove or Classify\nproved, mismatch, partial,\nunsupported, source-incomplete", 1000, 95, 245, 135, "#fce7f3"),
            Box("improve", "Improve Coverage\nnew fixtures, budgets,\nledger updates", 1000, 335, 245, 115, "#ede9fe"),
        ),
        edges=(
            Edge("detect", "model", "AST facts"),
            Edge("detect", "mineir", "debug slice"),
            Edge("model", "check", "semantic intent"),
            Edge("mineir", "check", "implementation evidence"),
            Edge("check", "prove", "obligations"),
            Edge("prove", "improve", "gaps"),
            Edge("improve", "detect", "next target"),
        ),
    ),
)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    for diagram in DIAGRAMS:
        svg_path = OUT / f"{diagram.name}.svg"
        drawio_path = OUT / f"{diagram.name}.drawio"
        png_path = OUT / f"{diagram.name}.png"
        svg_path.write_text(svg_for(diagram), encoding="utf-8")
        drawio_path.write_text(drawio_for(diagram), encoding="utf-8")
        subprocess.run(
            ["rsvg-convert", str(svg_path), "-o", str(png_path)],
            check=True,
        )
        generated.extend([drawio_path, svg_path, png_path])
    for path in generated:
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
