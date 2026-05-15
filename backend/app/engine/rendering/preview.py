from __future__ import annotations

# CLAUDE-GUARD:
# Render crop must follow isolated architectural bbox, not the full paper/page bbox.

import math
from pathlib import Path

import ezdxf
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Polygon

from app.engine.types import BBox, Orientation


def _draw_arrow(ax, tip: tuple[float, float], direction: tuple[float, float], size: float) -> None:
    x, y = tip
    dx, dy = direction
    norm = math.hypot(dx, dy)
    if norm == 0:
        return
    dx, dy = dx / norm, dy / norm
    px, py = -dy, dx
    points = np.array(
        [
            [x, y],
            [x - dx * size + px * size * 0.30, y - dy * size + py * size * 0.30],
            [x - dx * size - px * size * 0.30, y - dy * size - py * size * 0.30],
        ]
    )
    ax.add_patch(Polygon(points, closed=True, facecolor="black", edgecolor="black", linewidth=0.18))


def _extract_preview(doc: ezdxf.EzDxfDocument):
    segments = []
    dimensions = []

    def add_segment(x1, y1, x2, y2, layer):
        length = math.hypot(x2 - x1, y2 - y1)
        if length > 1e-6:
            segments.append((float(x1), float(y1), float(x2), float(y2), layer, length))

    for entity in doc.modelspace():
        kind = entity.dxftype()
        layer = getattr(entity.dxf, "layer", "")
        try:
            if kind == "LINE":
                a, b = entity.dxf.start, entity.dxf.end
                add_segment(a.x, a.y, b.x, b.y, layer)
            elif kind == "LWPOLYLINE":
                points = [(p[0], p[1]) for p in entity.get_points()]
                if entity.closed and points:
                    points.append(points[0])
                for a, b in zip(points, points[1:]):
                    add_segment(a[0], a[1], b[0], b[1], layer)
            elif kind == "POLYLINE":
                points = [(v.dxf.location.x, v.dxf.location.y) for v in entity.vertices]
                if entity.is_closed and points:
                    points.append(points[0])
                for a, b in zip(points, points[1:]):
                    add_segment(a[0], a[1], b[0], b[1], layer)
            elif kind == "DIMENSION":
                p1 = entity.dxf.defpoint2
                p2 = entity.dxf.defpoint3
                base = entity.dxf.defpoint
                angle = float(getattr(entity.dxf, "angle", 0) or 0)
                dimensions.append(
                    {
                        "p1": (float(p1.x), float(p1.y)),
                        "p2": (float(p2.x), float(p2.y)),
                        "base": (float(base.x), float(base.y)),
                        "angle": angle,
                    }
                )
        except Exception:
            continue
    return segments, dimensions


def render_preview(dxf_path: str | Path, png_path: str | Path, pdf_path: str | Path, bbox: BBox) -> None:
    doc = ezdxf.readfile(str(dxf_path))
    segments, dimensions = _extract_preview(doc)

    base = max(1.0, min(bbox.width, bbox.height))
    pad = max(bbox.width, bbox.height) * 0.17
    arrow_size = max(7.0, base * 0.0038)
    font_size = 3.4
    text_offset = max(12.0, base * 0.0065)

    fig, ax = plt.subplots(figsize=(16, 11), facecolor="white")

    for x1, y1, x2, y2, layer, _length in segments:
        if (bbox.x1 - pad <= x1 <= bbox.x2 + pad and bbox.y1 - pad <= y1 <= bbox.y2 + pad) or (
            bbox.x1 - pad <= x2 <= bbox.x2 + pad and bbox.y1 - pad <= y2 <= bbox.y2 + pad
        ):
            if "DIM" not in layer.upper() and "NAVVIX" not in layer.upper():
                ax.plot([x1, x2], [y1, y2], color="black", linewidth=0.9)

    for dim in dimensions:
        p1, p2, base_point = dim["p1"], dim["p2"], dim["base"]
        if not (bbox.x1 - pad <= base_point[0] <= bbox.x2 + pad and bbox.y1 - pad <= base_point[1] <= bbox.y2 + pad):
            continue

        is_h = abs(dim["angle"]) < 45 or abs(dim["angle"] - 180) < 45
        if is_h:
            xa, xb = p1[0], p2[0]
            y = base_point[1]
            value = str(int(round(abs(xb - xa))))
            side = 1 if y >= (bbox.y1 + bbox.y2) / 2 else -1
            ax.plot([xa, xb], [y, y], color="black", linewidth=0.38)
            ax.plot([xa, xa], [p1[1], y], color="black", linewidth=0.22)
            ax.plot([xb, xb], [p2[1], y], color="black", linewidth=0.22)
            _draw_arrow(ax, (xa, y), (1, 0), arrow_size)
            _draw_arrow(ax, (xb, y), (-1, 0), arrow_size)
            tx = (xa + xb) / 2
            if abs(xb - xa) < len(value) * 9 + 3 * arrow_size:
                tx = xb + len(value) * 5 + arrow_size
            ax.text(tx, y + side * text_offset, value, fontsize=font_size, ha="center", va="center")
        else:
            ya, yb = p1[1], p2[1]
            x = base_point[0]
            value = str(int(round(abs(yb - ya))))
            side = 1 if x >= (bbox.x1 + bbox.x2) / 2 else -1
            ax.plot([x, x], [ya, yb], color="black", linewidth=0.38)
            ax.plot([p1[0], x], [ya, ya], color="black", linewidth=0.22)
            ax.plot([p2[0], x], [yb, yb], color="black", linewidth=0.22)
            _draw_arrow(ax, (x, ya), (0, 1), arrow_size)
            _draw_arrow(ax, (x, yb), (0, -1), arrow_size)
            ty = (ya + yb) / 2
            if abs(yb - ya) < len(value) * 9 + 3 * arrow_size:
                ty = yb + len(value) * 5 + arrow_size
            ax.text(x + side * text_offset, ty, value, fontsize=font_size, ha="center", va="center", rotation=90)

    ax.set_xlim(bbox.x1 - pad, bbox.x2 + pad)
    ax.set_ylim(bbox.y1 - pad, bbox.y2 + pad)
    ax.set_aspect("equal")
    ax.axis("off")

    fig.savefig(png_path, dpi=280, bbox_inches="tight", facecolor="white")
    fig.savefig(pdf_path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
