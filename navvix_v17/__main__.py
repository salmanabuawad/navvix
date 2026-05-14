"""
navvix_v17 — Main-plan isolation + ISO-25 compact dimension engine.

Verbatim port of ChatGPT's v17 working baseline (CLAUDE_PROMPT.md):
  • Isolate the main architectural floorplan first
  • Exclude legend/table/title/frame
  • Rebuild dimensions only on the isolated main plan
  • Semantic-merge edges to avoid tiny transition dimensions
  • ISO-25 compact dimension style (dimtxt=16, dimasz=16, dimgap=7)

CLI:
  python -m navvix_v17 --input floor.dxf --output-dir out/
"""

import argparse
import json
import math
import shutil
from pathlib import Path

import ezdxf
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
from ezdxf import units


def entity_points(e):
    t = e.dxftype()
    pts = []
    try:
        if t == "LINE":
            pts = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
        elif t == "LWPOLYLINE":
            pts = [(p[0], p[1]) for p in e.get_points()]
        elif t == "POLYLINE":
            pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
        elif t in ("TEXT", "MTEXT", "INSERT"):
            p = e.dxf.insert
            pts = [(p.x, p.y)]
        elif t == "DIMENSION":
            for attr in ["defpoint", "defpoint2", "defpoint3"]:
                if hasattr(e.dxf, attr):
                    p = getattr(e.dxf, attr)
                    pts.append((p.x, p.y))
    except Exception:
        pass
    return pts


def bbox(points):
    xs = [x for x, y in points]
    ys = [y for x, y in points]
    return min(xs), min(ys), max(xs), max(ys)


def intersects(a, b):
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def contains(b, p):
    return b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]


def copy_layers(src_doc, dst_doc):
    for layer in src_doc.layers:
        try:
            name = layer.dxf.name
            if name not in dst_doc.layers:
                dst_doc.layers.new(name, dxfattribs={"color": layer.dxf.color})
        except Exception:
            pass


def setup_dimstyle(doc, style="ISO-25"):
    doc.units = units.MM

    if style not in doc.dimstyles:
        doc.dimstyles.new(style)

    ds = doc.dimstyles.get(style)

    settings = {
        "dimtxt": 16.0,
        "dimasz": 16.0,
        "dimgap": 7.0,
        "dimtad": 1,
        "dimjust": 0,
        "dimtih": 0,
        "dimtoh": 0,
        "dimexe": 1.25,
        "dimexo": 0.625,
    }

    for key, value in settings.items():
        try:
            setattr(ds.dxf, key, value)
        except Exception:
            pass

    # Allow AutoCAD/CAD viewers to place text/arrows cleanly when space is tight.
    for key, value in [("dimtix", 0), ("dimtofl", 1), ("dimtmove", 1), ("dimatfit", 3)]:
        try:
            setattr(ds.dxf, key, value)
        except Exception:
            pass

    return style


def collect_records(doc):
    records = []
    for e in doc.modelspace():
        pts = entity_points(e)
        if not pts:
            continue
        b = bbox(pts)
        records.append(
            {
                "entity": e,
                "type": e.dxftype(),
                "layer": getattr(e.dxf, "layer", ""),
                "bbox": b,
                "center": ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2),
                "w": b[2] - b[0],
                "h": b[3] - b[1],
            }
        )
    return records


def detect_main_plan_cluster(records):
    all_points = []
    for r in records:
        b = r["bbox"]
        all_points += [(b[0], b[1]), (b[2], b[3])]

    global_bbox = bbox(all_points)
    gx1, gy1, gx2, gy2 = global_bbox
    gw, gh = gx2 - gx1, gy2 - gy1

    seeds = [
        r
        for r in records
        if r["type"] in ("LINE", "LWPOLYLINE", "POLYLINE") and max(r["w"], r["h"]) > 1
    ]

    if not seeds:
        raise RuntimeError("No vector geometry found.")

    centers = np.array([r["center"] for r in seeds], dtype=float)
    eps = max(180.0, min(gw, gh) * 0.022)

    visited = np.zeros(len(seeds), dtype=bool)
    clusters = []

    for i in range(len(seeds)):
        if visited[i]:
            continue

        stack = [i]
        visited[i] = True
        comp = []

        while stack:
            j = stack.pop()
            comp.append(j)

            distances = np.sqrt(((centers - centers[j]) ** 2).sum(axis=1))
            for k in np.where(distances <= eps)[0]:
                if not visited[k]:
                    visited[k] = True
                    stack.append(int(k))

        clusters.append(comp)

    infos = []

    for cid, comp in enumerate(clusters):
        xs, ys = [], []
        h_count = 0
        v_count = 0

        for idx in comp:
            r = seeds[idx]
            b = r["bbox"]
            xs += [b[0], b[2]]
            ys += [b[1], b[3]]

            if r["w"] >= r["h"] * 8:
                h_count += 1
            elif r["h"] >= r["w"] * 8:
                v_count += 1

        b = min(xs), min(ys), max(xs), max(ys)
        bw, bh = b[2] - b[0], b[3] - b[1]
        area = max(1.0, bw * bh)
        cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
        nx, ny = (cx - gx1) / max(1, gw), (cy - gy1) / max(1, gh)
        aspect = max(bw, bh) / max(1, min(bw, bh))
        count = len(comp)
        grid_score = (h_count + v_count) / max(1, count)

        right_or_bottom = nx > 0.68 or ny < 0.20 or ny > 0.82
        table_like = grid_score > 0.88 and (right_or_bottom or aspect > 2.5)
        frame_like = bw > gw * 0.70 and bh > gh * 0.55 and count < 120

        # Good main plans are dense/irregular and near the center-left of the sheet.
        score = (count + area / max(1, gw * gh) * 250) * max(
            0.1, 1.4 - abs(nx - 0.45) * 2 - abs(ny - 0.52) * 1.4
        )

        if table_like:
            score *= 0.03
        if frame_like:
            score *= 0.03
        if count < 20:
            score *= 0.05

        infos.append(
            {
                "id": cid,
                "bbox": b,
                "count": count,
                "score": score,
                "table_like": table_like,
                "frame_like": frame_like,
                "nx": nx,
                "ny": ny,
                "aspect": aspect,
            }
        )

    infos = sorted(infos, key=lambda x: x["score"], reverse=True)
    main = infos[0]

    mb = main["bbox"]
    pad = max(80.0, min(mb[2] - mb[0], mb[3] - mb[1]) * 0.045)
    work_bbox = (mb[0] - pad, mb[1] - pad, mb[2] + pad, mb[3] + pad)

    return global_bbox, main, work_bbox, infos


def isolate_main_plan(input_dxf, output_dxf):
    doc = ezdxf.readfile(str(input_dxf))
    records = collect_records(doc)
    global_bbox, main, work_bbox, cluster_infos = detect_main_plan_cluster(records)

    gx1, gy1, gx2, gy2 = global_bbox
    gw, gh = gx2 - gx1, gy2 - gy1

    isolated = ezdxf.new(dxfversion=doc.dxfversion)
    isolated.units = doc.units
    copy_layers(doc, isolated)
    imsp = isolated.modelspace()

    copied = 0

    for r in records:
        # Previous generated dimensions and annotations must not drive new wall extraction.
        if r["type"] == "DIMENSION":
            continue

        if intersects(r["bbox"], work_bbox) or contains(work_bbox, r["center"]):
            # Remove giant full-frame rectangles.
            if r["w"] > gw * 0.75 and r["h"] > gh * 0.50:
                continue

            try:
                imsp.add_entity(r["entity"].copy())
                copied += 1
            except Exception:
                pass

    Path(output_dxf).parent.mkdir(parents=True, exist_ok=True)
    isolated.saveas(str(output_dxf))

    return {
        "global_bbox": global_bbox,
        "main_cluster": main,
        "work_bbox": work_bbox,
        "cluster_count": len(cluster_infos),
        "copied_entities": copied,
    }


def extract_edges(doc):
    raw = []

    def add_edge(x1, y1, x2, y2, layer):
        lu = layer.upper()
        if "DIM" in lu or "V17" in lu:
            return

        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)

        if length < 25:
            return

        if abs(dx) >= abs(dy) * 8:
            a, b = sorted([float(x1), float(x2)])
            c = float((y1 + y2) / 2)
            raw.append({"ori": "H", "a": a, "b": b, "c": c, "len": b - a})
        elif abs(dy) >= abs(dx) * 8:
            a, b = sorted([float(y1), float(y2)])
            c = float((x1 + x2) / 2)
            raw.append({"ori": "V", "a": a, "b": b, "c": c, "len": b - a})

    for e in doc.modelspace():
        t = e.dxftype()
        layer = getattr(e.dxf, "layer", "")

        try:
            if t == "LINE":
                a, b = e.dxf.start, e.dxf.end
                add_edge(a.x, a.y, b.x, b.y, layer)
            elif t == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points()]
                if e.closed and pts:
                    pts.append(pts[0])
                for a, b in zip(pts, pts[1:]):
                    add_edge(a[0], a[1], b[0], b[1], layer)
            elif t == "POLYLINE":
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                if e.is_closed and pts:
                    pts.append(pts[0])
                for a, b in zip(pts, pts[1:]):
                    add_edge(a[0], a[1], b[0], b[1], layer)
        except Exception:
            pass

    return raw


def semantic_merge_edges(raw_edges):
    if not raw_edges:
        raise RuntimeError("No wall edges extracted from isolated plan.")

    xs, ys = [], []

    for ed in raw_edges:
        if ed["ori"] == "H":
            xs += [ed["a"], ed["b"]]
            ys.append(ed["c"])
        else:
            xs.append(ed["c"])
            ys += [ed["a"], ed["b"]]

    xlo, xhi = np.percentile(xs, [1, 99])
    ylo, yhi = np.percentile(ys, [1, 99])
    width, height = xhi - xlo, yhi - ylo
    base = min(width, height)

    snap = max(8.0, base * 0.003)

    def sv(v):
        return round(v / snap) * snap

    dedup = {}

    for ed in raw_edges:
        if ed["len"] < max(45, base * 0.010):
            continue

        key = (ed["ori"], sv(ed["c"]), sv(ed["a"]), sv(ed["b"]))

        if key not in dedup or ed["len"] > dedup[key]["len"]:
            dedup[key] = {
                "ori": ed["ori"],
                "a": sv(ed["a"]),
                "b": sv(ed["b"]),
                "c": sv(ed["c"]),
                "len": abs(sv(ed["b"]) - sv(ed["a"])),
            }

    edges = list(dedup.values())

    short_threshold = max(115.0, base * 0.030)
    perimeter_band = max(90.0, base * 0.030)
    gap_tol = max(18.0, base * 0.006)

    def is_perimeter(ed):
        if ed["ori"] == "H":
            return min(abs(ed["c"] - ylo), abs(ed["c"] - yhi)) <= perimeter_band
        return min(abs(ed["c"] - xlo), abs(ed["c"] - xhi)) <= perimeter_band

    groups = {}
    for ed in edges:
        groups.setdefault((ed["ori"], sv(ed["c"])), []).append(ed)

    semantic = []
    filtered_out = []

    for key, arr in groups.items():
        arr = sorted(arr, key=lambda e: e["a"])
        cur = None

        for ed in arr:
            if ed["len"] < short_threshold and not is_perimeter(ed):
                filtered_out.append({**ed, "reason": "short_non_perimeter_transition_or_table_noise"})
                continue

            if cur is None:
                cur = ed.copy()
                continue

            if ed["a"] - cur["b"] <= gap_tol:
                cur["b"] = max(cur["b"], ed["b"])
                cur["len"] = cur["b"] - cur["a"]
            else:
                semantic.append(cur)
                cur = ed.copy()

        if cur:
            semantic.append(cur)

    semantic = [ed for ed in semantic if ed["len"] >= max(60.0, base * 0.014)]

    def sort_key(ed):
        if ed["ori"] == "H":
            return (min(abs(ed["c"] - ylo), abs(ed["c"] - yhi)), 0, ed["c"], ed["a"])
        return (min(abs(ed["c"] - xlo), abs(ed["c"] - xhi)), 1, ed["c"], ed["a"])

    return sorted(semantic, key=sort_key), filtered_out, (xlo, ylo, xhi, yhi), base


def add_dimensions(doc, semantic_edges, bbox_values, base):
    xlo, ylo, xhi, yhi = bbox_values
    msp = doc.modelspace()

    style = setup_dimstyle(doc, "ISO-25")

    layer = "V17_MAIN_PLAN_DIMS"
    if layer not in doc.layers:
        doc.layers.new(layer, dxfattribs={"color": 7})

    offset0 = max(42.0, base * 0.022)
    created = []

    for i, ed in enumerate(semantic_edges):
        off = offset0 * (1 + (i % 3) * 0.45)

        try:
            if ed["ori"] == "H":
                a, b, c = ed["a"], ed["b"], ed["c"]
                side = 1 if c >= (ylo + yhi) / 2 else -1
                basept = ((a + b) / 2, c + side * off)

                dim = msp.add_linear_dim(
                    base=basept,
                    p1=(a, c),
                    p2=(b, c),
                    angle=0,
                    dimstyle=style,
                    dxfattribs={"layer": layer},
                )
                dim.render()
                created.append({"ori": "H", "p1": [a, c], "p2": [b, c], "base": list(basept), "length": ed["len"]})
            else:
                a, b, c = ed["a"], ed["b"], ed["c"]
                side = 1 if c >= (xlo + xhi) / 2 else -1
                basept = (c + side * off, (a + b) / 2)

                dim = msp.add_linear_dim(
                    base=basept,
                    p1=(c, a),
                    p2=(c, b),
                    angle=90,
                    dimstyle=style,
                    dxfattribs={"layer": layer},
                )
                dim.render()
                created.append({"ori": "V", "p1": [c, a], "p2": [c, b], "base": list(basept), "length": ed["len"]})
        except Exception as ex:
            created.append({"error": str(ex), "edge": ed})

    return created


def extract_preview_geometry(doc):
    segments = []
    dims = []

    def add_segment(x1, y1, x2, y2, layer):
        length = math.hypot(x2 - x1, y2 - y1)
        if length > 1e-6:
            segments.append((float(x1), float(y1), float(x2), float(y2), layer, length))

    for e in doc.modelspace():
        t = e.dxftype()
        layer = getattr(e.dxf, "layer", "")

        try:
            if t == "LINE":
                a, b = e.dxf.start, e.dxf.end
                add_segment(a.x, a.y, b.x, b.y, layer)
            elif t == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points()]
                if e.closed and pts:
                    pts.append(pts[0])
                for a, b in zip(pts, pts[1:]):
                    add_segment(a[0], a[1], b[0], b[1], layer)
            elif t == "POLYLINE":
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                if e.is_closed and pts:
                    pts.append(pts[0])
                for a, b in zip(pts, pts[1:]):
                    add_segment(a[0], a[1], b[0], b[1], layer)
            elif t == "DIMENSION":
                p1 = e.dxf.defpoint2
                p2 = e.dxf.defpoint3
                base = e.dxf.defpoint
                dims.append(
                    {
                        "p1": (float(p1.x), float(p1.y)),
                        "p2": (float(p2.x), float(p2.y)),
                        "base": (float(base.x), float(base.y)),
                        "angle": float(getattr(e.dxf, "angle", 0) or 0),
                    }
                )
        except Exception:
            pass

    return segments, dims


def draw_arrow(ax, tip, direction, size):
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


def dimension_value(d):
    p1, p2 = d["p1"], d["p2"]
    is_h = abs(d["angle"]) < 45 or abs(d["angle"] - 180) < 45
    return str(int(round(abs(p2[0] - p1[0]) if is_h else abs(p2[1] - p1[1]))))


def render_preview(dxf_path, output_png, output_pdf, bbox_values, base):
    doc = ezdxf.readfile(str(dxf_path))
    segments, dims = extract_preview_geometry(doc)

    xlo, ylo, xhi, yhi = bbox_values
    width, height = xhi - xlo, yhi - ylo
    pad = max(width, height) * 0.17
    arrow_size = max(7, base * 0.0038)
    font_size = 3.4
    text_offset = max(12, base * 0.0065)

    fig, ax = plt.subplots(figsize=(16, 11), facecolor="white")

    for x1, y1, x2, y2, layer, length in segments:
        if (xlo - pad <= x1 <= xhi + pad and ylo - pad <= y1 <= yhi + pad) or (
            xlo - pad <= x2 <= xhi + pad and ylo - pad <= y2 <= yhi + pad
        ):
            if "DIM" not in layer.upper() and "V17" not in layer.upper():
                ax.plot([x1, x2], [y1, y2], color="black", linewidth=0.9)

    for d in dims:
        p1, p2, basept = d["p1"], d["p2"], d["base"]

        if not (xlo - pad <= basept[0] <= xhi + pad and ylo - pad <= basept[1] <= yhi + pad):
            continue

        is_h = abs(d["angle"]) < 45 or abs(d["angle"] - 180) < 45
        text = dimension_value(d)

        if is_h:
            xa, xb = p1[0], p2[0]
            y = basept[1]
            side = 1 if y >= (ylo + yhi) / 2 else -1

            ax.plot([xa, xb], [y, y], color="black", linewidth=0.38)
            ax.plot([xa, xa], [p1[1], y], color="black", linewidth=0.22)
            ax.plot([xb, xb], [p2[1], y], color="black", linewidth=0.22)
            draw_arrow(ax, (xa, y), (1, 0), arrow_size)
            draw_arrow(ax, (xb, y), (-1, 0), arrow_size)

            tx = (xa + xb) / 2
            if abs(xb - xa) < len(text) * 9 + 3 * arrow_size:
                tx = xb + len(text) * 5 + arrow_size

            ax.text(tx, y + side * text_offset, text, fontsize=font_size, ha="center", va="center")
        else:
            ya, yb = p1[1], p2[1]
            x = basept[0]
            side = 1 if x >= (xlo + xhi) / 2 else -1

            ax.plot([x, x], [ya, yb], color="black", linewidth=0.38)
            ax.plot([p1[0], x], [ya, ya], color="black", linewidth=0.22)
            ax.plot([p2[0], x], [yb, yb], color="black", linewidth=0.22)
            draw_arrow(ax, (x, ya), (0, 1), arrow_size)
            draw_arrow(ax, (x, yb), (0, -1), arrow_size)

            ty = (ya + yb) / 2
            if abs(yb - ya) < len(text) * 9 + 3 * arrow_size:
                ty = yb + len(text) * 5 + arrow_size

            ax.text(x + side * text_offset, ty, text, fontsize=font_size, ha="center", va="center", rotation=90)

    ax.set_xlim(xlo - pad, xhi + pad)
    ax.set_ylim(ylo - pad, yhi + pad)
    ax.set_aspect("equal")
    ax.axis("off")

    fig.savefig(output_png, dpi=280, bbox_inches="tight", facecolor="white")
    fig.savefig(output_pdf, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def process(input_dxf, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    isolated_dxf = output_dir / "v17_isolated_main_plan_only.dxf"
    dimensioned_dxf = output_dir / "v17_main_plan_only_dimensioned.dxf"
    preview_png = output_dir / "v17_main_plan_only_preview.png"
    preview_pdf = output_dir / "v17_main_plan_only_preview.pdf"
    report_path = output_dir / "v17_main_plan_only_report.json"

    isolation = isolate_main_plan(input_dxf, isolated_dxf)

    doc = ezdxf.readfile(str(isolated_dxf))
    setup_dimstyle(doc)

    raw_edges = extract_edges(doc)
    semantic_edges, filtered_out, bbox_values, base = semantic_merge_edges(raw_edges)
    created = add_dimensions(doc, semantic_edges, bbox_values, base)

    doc.saveas(str(dimensioned_dxf))

    render_preview(dimensioned_dxf, preview_png, preview_pdf, bbox_values, base)

    h_count = sum(1 for ed in semantic_edges if ed["ori"] == "H")
    v_count = sum(1 for ed in semantic_edges if ed["ori"] == "V")

    report = {
        "input": str(input_dxf),
        "isolated_main_plan_dxf": str(isolated_dxf),
        "dimensioned_dxf": str(dimensioned_dxf),
        "preview_png": str(preview_png),
        "preview_pdf": str(preview_pdf),
        "isolation": isolation,
        "raw_edges": len(raw_edges),
        "semantic_edges": len(semantic_edges),
        "filtered_out_edges": len(filtered_out),
        "dimensions_created": len([x for x in created if "error" not in x]),
        "x_axes": h_count,
        "y_axes": v_count,
        "style": {"dimstyle": "ISO-25", "dimtxt": 16.0, "dimasz": 16.0, "dimgap": 7.0},
        "rule": "V17 isolates main floorplan first, excludes legend/table/title/frame before dimension generation.",
    }

    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    result = process(args.input, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
