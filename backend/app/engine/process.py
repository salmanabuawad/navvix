from __future__ import annotations

# CLAUDE-GUARD:
# This is the production orchestrator. Keep it modular.
# Do not reintroduce versioned engines or monolithic scripts.

import json
from dataclasses import asdict
from pathlib import Path

import ezdxf

from app.engine.dimensions.generator import add_dimensions
from app.engine.dimensions.ownership import candidates_from_semantic_spans
from app.engine.geometry.line_registry import extract_line_registry, registry_bbox
from app.engine.isolation.main_plan import copy_isolated_doc
from app.engine.rendering.preview import render_preview
from app.engine.semantic.wall_graph import semantic_spans_from_lines
from app.engine.styling.dimstyle import setup_dimstyle
from app.engine.validation.quality import validate_dimensions


def process_dxf(input_path: str | Path, output_dir: str | Path) -> dict:
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    isolated_path = output_dir / "isolated_main_plan.dxf"
    dimensioned_path = output_dir / "dimensioned.dxf"
    preview_png = output_dir / "preview.png"
    preview_pdf = output_dir / "preview.pdf"
    report_path = output_dir / "report.json"

    isolation_report = copy_isolated_doc(str(input_path), str(isolated_path))

    doc = ezdxf.readfile(str(isolated_path))
    style_name = setup_dimstyle(doc)

    lines = extract_line_registry(doc)
    bbox = registry_bbox(lines)
    spans, filtered = semantic_spans_from_lines(lines, bbox)
    candidates = candidates_from_semantic_spans(spans, bbox)
    created = add_dimensions(doc, candidates, style_name=style_name)
    validation = validate_dimensions(candidates)

    doc.saveas(str(dimensioned_path))
    render_preview(dimensioned_path, preview_png, preview_pdf, bbox)

    report = {
        "input": str(input_path),
        "isolated_dxf": str(isolated_path),
        "dimensioned_dxf": str(dimensioned_path),
        "preview_png": str(preview_png),
        "preview_pdf": str(preview_pdf),
        "line_count": len(lines),
        "semantic_span_count": len(spans),
        "filtered_span_count": len(filtered),
        "dimensions_created": len([d for d in created if "error" not in d]),
        "bbox": asdict(bbox),
        "style": {"dimstyle": style_name},
        "isolation": isolation_report,
        "validation": validation,
        "dimensions": created,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report
