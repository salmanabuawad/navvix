from __future__ import annotations

# CLAUDE-GUARD:
# This module builds the normalized line inventory only.
# Do not generate dimensions here.

import math
from itertools import count

import ezdxf
import numpy as np

from app.engine.types import LineSegment, Orientation, BBox


def _orientation(dx: float, dy: float) -> Orientation:
    if abs(dx) >= abs(dy) * 8:
        return Orientation.H
    if abs(dy) >= abs(dx) * 8:
        return Orientation.V
    return Orientation.OTHER


def _add_segment(lines: list[LineSegment], seq, x1: float, y1: float, x2: float, y2: float, layer: str) -> None:
    lu = layer.upper()
    if "DIM" in lu or "V17" in lu or "NAVVIX" in lu:
        return
    dx = x2 - x1
    dy = y2 - y1
    length = math.hypot(dx, dy)
    if length < 25:
        return
    ori = _orientation(dx, dy)
    if ori == Orientation.OTHER:
        return
    lines.append(
        LineSegment(
            id=f"L-{next(seq):05d}",
            orientation=ori,
            x1=float(x1),
            y1=float(y1),
            x2=float(x2),
            y2=float(y2),
            length=float(length),
            layer=layer,
        )
    )


def extract_line_registry(doc: ezdxf.EzDxfDocument) -> list[LineSegment]:
    lines: list[LineSegment] = []
    seq = count(1)
    for e in doc.modelspace():
        kind = e.dxftype()
        layer = getattr(e.dxf, "layer", "")
        try:
            if kind == "LINE":
                a, b = e.dxf.start, e.dxf.end
                _add_segment(lines, seq, a.x, a.y, b.x, b.y, layer)
            elif kind == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points()]
                if e.closed and pts:
                    pts.append(pts[0])
                for a, b in zip(pts, pts[1:]):
                    _add_segment(lines, seq, a[0], a[1], b[0], b[1], layer)
            elif kind == "POLYLINE":
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                if e.is_closed and pts:
                    pts.append(pts[0])
                for a, b in zip(pts, pts[1:]):
                    _add_segment(lines, seq, a[0], a[1], b[0], b[1], layer)
        except Exception:
            continue
    return lines


def registry_bbox(lines: list[LineSegment]) -> BBox:
    if not lines:
        raise ValueError("No line segments extracted from isolated plan")
    xs: list[float] = []
    ys: list[float] = []
    for line in lines:
        xs.extend([line.x1, line.x2])
        ys.extend([line.y1, line.y2])
    return BBox(
        float(np.percentile(xs, 1)),
        float(np.percentile(ys, 1)),
        float(np.percentile(xs, 99)),
        float(np.percentile(ys, 99)),
    )
