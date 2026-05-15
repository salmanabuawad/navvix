from __future__ import annotations

# CLAUDE-GUARD:
# This module exists to prevent table/title/frame geometry from entering the wall engine.
# Do not remove hard rejection of page frames and sparse border-only clusters.

from dataclasses import asdict
from typing import Any

import ezdxf
import numpy as np

from app.engine.types import BBox, EntityRecord


def _points_for_entity(entity: Any) -> list[tuple[float, float]]:
    kind = entity.dxftype()
    points: list[tuple[float, float]] = []
    try:
        if kind == "LINE":
            points = [(entity.dxf.start.x, entity.dxf.start.y), (entity.dxf.end.x, entity.dxf.end.y)]
        elif kind == "LWPOLYLINE":
            points = [(p[0], p[1]) for p in entity.get_points()]
        elif kind == "POLYLINE":
            points = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
        elif kind in ("TEXT", "MTEXT", "INSERT"):
            p = entity.dxf.insert
            points = [(p.x, p.y)]
        elif kind == "DIMENSION":
            for attr in ["defpoint", "defpoint2", "defpoint3"]:
                if hasattr(entity.dxf, attr):
                    p = getattr(entity.dxf, attr)
                    points.append((p.x, p.y))
    except Exception:
        return []
    return points


def _bbox(points: list[tuple[float, float]]) -> BBox:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return BBox(min(xs), min(ys), max(xs), max(ys))


def collect_records(doc: ezdxf.EzDxfDocument) -> list[EntityRecord]:
    records: list[EntityRecord] = []
    for entity in doc.modelspace():
        points = _points_for_entity(entity)
        if not points:
            continue
        records.append(
            EntityRecord(
                entity=entity,
                dxftype=entity.dxftype(),
                layer=getattr(entity.dxf, "layer", ""),
                bbox=_bbox(points),
            )
        )
    return records


def global_bbox(records: list[EntityRecord]) -> BBox:
    points: list[tuple[float, float]] = []
    for record in records:
        b = record.bbox
        points.extend([(b.x1, b.y1), (b.x2, b.y2)])
    return _bbox(points)


def detect_main_plan_bbox(records: list[EntityRecord]) -> tuple[BBox, dict]:
    """Detect the main architectural cluster.

    This intentionally rejects giant frames, right-side title blocks, legends, tables,
    and sparse rectangular page borders before picking the main plan.
    """
    gb = global_bbox(records)
    gw = max(1.0, gb.width)
    gh = max(1.0, gb.height)

    seeds = [
        r
        for r in records
        if r.dxftype in {"LINE", "LWPOLYLINE", "POLYLINE"}
        and max(r.bbox.width, r.bbox.height) > 1
    ]
    if not seeds:
        raise ValueError("No vector geometry found in DXF")

    centers = np.array([r.center for r in seeds], dtype=float)
    eps = max(180.0, min(gw, gh) * 0.022)
    visited = np.zeros(len(seeds), dtype=bool)
    clusters: list[list[int]] = []

    for i in range(len(seeds)):
        if visited[i]:
            continue
        stack = [i]
        visited[i] = True
        component: list[int] = []
        while stack:
            j = stack.pop()
            component.append(j)
            d = np.sqrt(((centers - centers[j]) ** 2).sum(axis=1))
            for k in np.where(d <= eps)[0]:
                if not visited[k]:
                    visited[k] = True
                    stack.append(int(k))
        clusters.append(component)

    cluster_infos: list[dict] = []
    for cid, component in enumerate(clusters):
        xs: list[float] = []
        ys: list[float] = []
        h_count = 0
        v_count = 0
        total_len = 0.0
        for idx in component:
            b = seeds[idx].bbox
            xs.extend([b.x1, b.x2])
            ys.extend([b.y1, b.y2])
            if b.width >= b.height * 8:
                h_count += 1
            elif b.height >= b.width * 8:
                v_count += 1
            total_len += max(b.width, b.height)

        b = BBox(min(xs), min(ys), max(xs), max(ys))
        area = max(1.0, b.width * b.height)
        cx, cy = b.center
        nx = (cx - gb.x1) / gw
        ny = (cy - gb.y1) / gh
        aspect = max(b.width, b.height) / max(1.0, min(b.width, b.height))
        count = len(component)
        grid_score = (h_count + v_count) / max(1, count)
        density = count / area

        # Hard rejections for page/frame/table dominance.
        giant_page_like = b.width > gw * 0.70 and b.height > gh * 0.70
        sparse_frame_like = giant_page_like and density < 0.00002
        right_or_bottom = nx > 0.68 or ny < 0.20 or ny > 0.82
        table_like = grid_score > 0.88 and (right_or_bottom or aspect > 2.5)
        frame_like = giant_page_like or sparse_frame_like

        score = (count + (total_len / 100.0))
        score *= max(0.1, 1.4 - abs(nx - 0.45) * 2.0 - abs(ny - 0.52) * 1.4)
        if table_like:
            score *= 0.02
        if frame_like:
            score *= 0.01
        if count < 20:
            score *= 0.05

        cluster_infos.append(
            {
                "id": cid,
                "bbox": asdict(b),
                "count": count,
                "score": score,
                "density": density,
                "grid_score": grid_score,
                "table_like": table_like,
                "frame_like": frame_like,
                "nx": nx,
                "ny": ny,
                "aspect": aspect,
            }
        )

    cluster_infos.sort(key=lambda c: c["score"], reverse=True)
    selected = cluster_infos[0]
    b = selected["bbox"]
    selected_bbox = BBox(b["x1"], b["y1"], b["x2"], b["y2"])
    pad = max(80.0, min(selected_bbox.width, selected_bbox.height) * 0.045)
    work_bbox = selected_bbox.expand(pad)

    return work_bbox, {
        "global_bbox": asdict(gb),
        "selected_cluster": selected,
        "cluster_count": len(cluster_infos),
        "work_bbox": asdict(work_bbox),
    }


def copy_isolated_doc(input_path: str, output_path: str) -> dict:
    doc = ezdxf.readfile(str(input_path))
    records = collect_records(doc)
    work_bbox, report = detect_main_plan_bbox(records)
    gb = global_bbox(records)

    out_doc = ezdxf.new(dxfversion=doc.dxfversion)
    out_doc.units = doc.units
    for layer in doc.layers:
        try:
            name = layer.dxf.name
            if name not in out_doc.layers:
                out_doc.layers.new(name, dxfattribs={"color": layer.dxf.color})
        except Exception:
            pass

    copied = 0
    msp = out_doc.modelspace()
    for record in records:
        if record.dxftype == "DIMENSION":
            continue
        if record.bbox.intersects(work_bbox) or work_bbox.contains_point(record.center):
            # Do not copy full-page frames into isolated geometry.
            if record.bbox.width > gb.width * 0.75 and record.bbox.height > gb.height * 0.50:
                continue
            try:
                msp.add_entity(record.entity.copy())
                copied += 1
            except Exception:
                pass

    out_doc.saveas(str(output_path))
    report["copied_entities"] = copied
    return report
