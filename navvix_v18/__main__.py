"""
navvix_v18 — Architectural-quality dimension engine.

Goals (vs v17):
  * Hard-reject page frames, title blocks, schedules, legends, tables BEFORE
    dimension generation (req #1).
  * Tight architectural crop bbox — no whitespace, no page area (req #2, #9).
  * Semantic wall merge that strips raw CAD artifacts (req #3).
  * Three-level dimension hierarchy — local / group / overall — at
    distinct offset planes (req #4).
  * Filled triangle arrows scaled to drawing size (req #5).
  * Centered text with outside-fallback when the span is too short (req #6).
  * Consistent close-to-wall spacing per level (req #7).
  * Basic final-output validation (req #10).

Deferred (full strength requires a multi-day topological-graph pass):
  * Req #8 full wall-graph cycle detection (room finding). v18 classifies
    edges via length quartile + perimeter band — a pragmatic proxy.
  * Req #6 full collision optimization. v18 does bbox-overlap detection
    per chain and bumps the dim to the next offset level.

CLI:
  python -m navvix_v18 --input plan.dxf --output-dir out/
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import ezdxf
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
from ezdxf import units


# ═══════════════════════════════════════════════════════════════════════════
# Geometry helpers
# ═══════════════════════════════════════════════════════════════════════════

def _entity_points(e):
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
            for attr in ("defpoint", "defpoint2", "defpoint3"):
                if hasattr(e.dxf, attr):
                    p = getattr(e.dxf, attr)
                    pts.append((p.x, p.y))
    except Exception:
        pass
    return pts


def _bbox(points):
    xs = [x for x, y in points]
    ys = [y for x, y in points]
    return min(xs), min(ys), max(xs), max(ys)


def _intersects(a, b):
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _contains(b, p):
    return b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]


def _copy_layers(src, dst):
    for lyr in src.layers:
        try:
            name = lyr.dxf.name
            if name not in dst.layers:
                dst.layers.new(name, dxfattribs={"color": lyr.dxf.color})
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# Isolation: hard-reject frames/tables/legends
# ═══════════════════════════════════════════════════════════════════════════

def _collect_records(doc):
    records = []
    for e in doc.modelspace():
        pts = _entity_points(e)
        if not pts:
            continue
        b = _bbox(pts)
        records.append({
            "entity": e,
            "type":   e.dxftype(),
            "layer":  getattr(e.dxf, "layer", ""),
            "bbox":   b,
            "center": ((b[0] + b[2]) / 2, (b[1] + b[3]) / 2),
            "w":      b[2] - b[0],
            "h":      b[3] - b[1],
        })
    return records


def _cluster_records(records):
    """DBSCAN-style proximity clustering. Returns list of (cluster_id, seed_indices)."""
    seeds = [r for r in records
             if r["type"] in ("LINE", "LWPOLYLINE", "POLYLINE")
             and max(r["w"], r["h"]) > 1]
    if not seeds:
        raise RuntimeError("No vector geometry found.")

    # Use ONLY vector geometry for the global bbox. Existing DIMENSION
    # entities can have extension defpoints far outside the actual walls,
    # which would inflate gw/gh and shrink eps relative to the real plan.
    geom_pts = []
    for r in seeds:
        b = r["bbox"]
        geom_pts += [(b[0], b[1]), (b[2], b[3])]
    global_bbox = _bbox(geom_pts)
    gx1, gy1, gx2, gy2 = global_bbox
    gw, gh = gx2 - gx1, gy2 - gy1

    centers = np.array([r["center"] for r in seeds], dtype=float)
    # Generous eps so a single apartment's walls don't fragment into pieces.
    eps = max(250.0, min(gw, gh) * 0.10)

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
            d = np.sqrt(((centers - centers[j]) ** 2).sum(axis=1))
            for k in np.where(d <= eps)[0]:
                if not visited[k]:
                    visited[k] = True
                    stack.append(int(k))
        clusters.append(comp)

    return seeds, clusters, global_bbox


def _score_clusters(seeds, clusters, global_bbox):
    """
    Score each cluster. HARD-REJECT clusters that look like frames, tables,
    or legends (req #1). Returns sorted list of (info, kept_records).
    """
    gx1, gy1, gx2, gy2 = global_bbox
    gw, gh = gx2 - gx1, gy2 - gy1
    g_area = max(1.0, gw * gh)

    infos = []
    for cid, comp in enumerate(clusters):
        xs, ys = [], []
        h_count = v_count = 0
        comp_records = [seeds[idx] for idx in comp]
        for r in comp_records:
            b = r["bbox"]
            xs += [b[0], b[2]]
            ys += [b[1], b[3]]
            if r["w"] >= r["h"] * 8:
                h_count += 1
            elif r["h"] >= r["w"] * 8:
                v_count += 1

        cb = (min(xs), min(ys), max(xs), max(ys))
        bw, bh = cb[2] - cb[0], cb[3] - cb[1]
        area = max(1.0, bw * bh)
        cx, cy = (cb[0] + cb[2]) / 2, (cb[1] + cb[3]) / 2
        nx = (cx - gx1) / max(1.0, gw)
        ny = (cy - gy1) / max(1.0, gh)
        aspect = max(bw, bh) / max(1.0, min(bw, bh))
        count = len(comp)
        grid_score = (h_count + v_count) / max(1, count)
        density = count / area
        relative_area = area / g_area

        # ── HARD REJECTION rules (req #1) ────────────────────────────────────
        # A frame is BIG + SPARSE. An apartment is BIG + DENSE. Combine size
        # with density so a standalone apartment (which fills its own file's
        # bbox) is NOT rejected as a frame.
        fills_global = (bw > gw * 0.70 and bh > gh * 0.70)
        is_mega = fills_global and density < 5e-6
        # Too few entities for a real plan.
        is_sparse = count < 12
        # Low density + large area = frame outline with title strip.
        is_low_density_giant = (relative_area > 0.40 and density < 1e-6)
        # Tall/narrow grid at the edges with low count = schedule table.
        is_table = (grid_score > 0.85
                    and (aspect > 2.4 or nx > 0.70 or ny > 0.85 or ny < 0.15)
                    and count < 80)
        # Frame-like: rectangular, proportional, AND sparse.
        is_frame = (bw > gw * 0.60 and bh > gh * 0.50
                    and density < 5e-6 and count < 80)

        hard_reject = is_mega or is_sparse or is_low_density_giant or is_table or is_frame
        reject_reasons = []
        if is_mega:             reject_reasons.append("mega_rectangle")
        if is_sparse:           reject_reasons.append("too_few_entities")
        if is_low_density_giant:reject_reasons.append("low_density_giant")
        if is_table:            reject_reasons.append("table_like")
        if is_frame:            reject_reasons.append("frame_like")

        # Score for sorting (only used among non-rejected).
        centrality = max(0.1, 1.5 - abs(nx - 0.45) * 2.0 - abs(ny - 0.52) * 1.4)
        score = (count + relative_area * 200) * centrality

        infos.append({
            "id":             cid,
            "bbox":           cb,
            "count":          count,
            "score":          score,
            "density":        density,
            "relative_area":  relative_area,
            "nx":             nx,
            "ny":             ny,
            "aspect":         aspect,
            "hard_reject":    hard_reject,
            "reject_reasons": reject_reasons,
            "records":        comp_records,
        })

    survivors = [i for i in infos if not i["hard_reject"]]
    rejected  = [i for i in infos if i["hard_reject"]]
    if not survivors:
        # Fallback: best of the rejected (avoid total failure).
        survivors = sorted(rejected, key=lambda x: x["score"], reverse=True)[:1]

    survivors = sorted(survivors, key=lambda x: x["score"], reverse=True)
    return survivors, rejected


def _tight_arch_bbox(records):
    """
    Compute a tight bbox from the records themselves, ignoring outliers.
    Uses 2nd-98th percentile of entity centers to drop any stray edges
    that slipped past clustering (req #2).
    """
    if not records:
        raise RuntimeError("No records for tight bbox.")
    pts = []
    for r in records:
        b = r["bbox"]
        pts += [(b[0], b[1]), (b[2], b[3])]
    xs = np.array([p[0] for p in pts], dtype=float)
    ys = np.array([p[1] for p in pts], dtype=float)
    xlo, xhi = np.percentile(xs, [1, 99])
    ylo, yhi = np.percentile(ys, [1, 99])
    return float(xlo), float(ylo), float(xhi), float(yhi)


def isolate_main_plan(input_dxf, output_dxf):
    doc = ezdxf.readfile(str(input_dxf))
    records = _collect_records(doc)
    seeds, clusters, global_bbox = _cluster_records(records)
    survivors, rejected = _score_clusters(seeds, clusters, global_bbox)
    main = survivors[0]

    arch_bbox = _tight_arch_bbox(main["records"])
    ax0, ay0, ax1, ay1 = arch_bbox
    aw, ah = ax1 - ax0, ay1 - ay0
    pad = max(40.0, min(aw, ah) * 0.025)
    work_bbox = (ax0 - pad, ay0 - pad, ax1 + pad, ay1 + pad)

    out_doc = ezdxf.new(dxfversion=doc.dxfversion)
    out_doc.units = doc.units
    _copy_layers(doc, out_doc)
    out_msp = out_doc.modelspace()

    gw = global_bbox[2] - global_bbox[0]
    gh = global_bbox[3] - global_bbox[1]
    copied = skipped_dim = skipped_frame = skipped_outside = 0
    for r in records:
        if r["type"] == "DIMENSION":
            skipped_dim += 1
            continue
        # Reject giant rectangles that slipped through (req #1).
        if r["w"] > gw * 0.65 and r["h"] > gh * 0.50:
            skipped_frame += 1
            continue
        # Require entity center inside work_bbox (req #2; v17 leak-fix).
        if not _contains(work_bbox, r["center"]):
            skipped_outside += 1
            continue
        try:
            out_msp.add_entity(r["entity"].copy())
            copied += 1
        except Exception:
            pass

    Path(output_dxf).parent.mkdir(parents=True, exist_ok=True)
    out_doc.saveas(str(output_dxf))

    return {
        "global_bbox":    global_bbox,
        "arch_bbox":      arch_bbox,
        "work_bbox":      work_bbox,
        "main_cluster":   {k: v for k, v in main.items() if k != "records"},
        "cluster_count":  len(seeds) and len(clusters),
        "rejected_count": len(rejected),
        "reject_reasons": [{"id": r["id"], "reasons": r["reject_reasons"]} for r in rejected],
        "copied":         copied,
        "skipped_dim":    skipped_dim,
        "skipped_frame":  skipped_frame,
        "skipped_outside":skipped_outside,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Wall edge extraction + semantic merge (req #3, #8 simplified)
# ═══════════════════════════════════════════════════════════════════════════

def _extract_edges(doc):
    raw = []

    def add(x1, y1, x2, y2, layer):
        lu = layer.upper()
        if "DIM" in lu or "V17" in lu or "V18" in lu:
            return
        dx, dy = x2 - x1, y2 - y1
        L = math.hypot(dx, dy)
        if L < 25:
            return
        if abs(dx) >= abs(dy) * 8:
            a, b = sorted([float(x1), float(x2)])
            raw.append({"ori": "H", "a": a, "b": b, "c": float((y1 + y2) / 2), "len": b - a})
        elif abs(dy) >= abs(dx) * 8:
            a, b = sorted([float(y1), float(y2)])
            raw.append({"ori": "V", "a": a, "b": b, "c": float((x1 + x2) / 2), "len": b - a})

    for e in doc.modelspace():
        layer = getattr(e.dxf, "layer", "")
        t = e.dxftype()
        try:
            if t == "LINE":
                a, b = e.dxf.start, e.dxf.end
                add(a.x, a.y, b.x, b.y, layer)
            elif t == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points()]
                if e.closed and pts:
                    pts.append(pts[0])
                for a, b in zip(pts, pts[1:]):
                    add(a[0], a[1], b[0], b[1], layer)
            elif t == "POLYLINE":
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                if e.is_closed and pts:
                    pts.append(pts[0])
                for a, b in zip(pts, pts[1:]):
                    add(a[0], a[1], b[0], b[1], layer)
        except Exception:
            pass
    return raw


def _semantic_merge(raw_edges, arch_bbox):
    if not raw_edges:
        raise RuntimeError("No wall edges in isolated plan.")

    xlo, ylo, xhi, yhi = arch_bbox
    base = max(1.0, min(xhi - xlo, yhi - ylo))

    snap = max(8.0, base * 0.003)
    short_threshold = max(115.0, base * 0.030)
    perimeter_band  = max(90.0,  base * 0.030)
    gap_tol         = max(18.0,  base * 0.006)
    final_min_len   = max(60.0,  base * 0.014)

    def sv(v):
        return round(v / snap) * snap

    # 1) Snap-to-grid dedup keeping the longest at each (ori, c, a, b) bucket.
    dedup = {}
    for ed in raw_edges:
        if ed["len"] < max(45.0, base * 0.010):
            continue
        key = (ed["ori"], sv(ed["c"]), sv(ed["a"]), sv(ed["b"]))
        cur = dedup.get(key)
        if cur is None or ed["len"] > cur["len"]:
            sa, sb = sv(ed["a"]), sv(ed["b"])
            dedup[key] = {"ori": ed["ori"], "a": sa, "b": sb,
                          "c": sv(ed["c"]), "len": abs(sb - sa)}
    edges = list(dedup.values())

    def is_perimeter(ed):
        if ed["ori"] == "H":
            return min(abs(ed["c"] - ylo), abs(ed["c"] - yhi)) <= perimeter_band
        return min(abs(ed["c"] - xlo), abs(ed["c"] - xhi)) <= perimeter_band

    # 2) Per-position merge of collinear touching edges; filter short non-perimeter.
    groups = {}
    for ed in edges:
        groups.setdefault((ed["ori"], sv(ed["c"])), []).append(ed)

    semantic = []
    filtered_out = []
    for arr in groups.values():
        arr.sort(key=lambda e: e["a"])
        cur = None
        for ed in arr:
            if ed["len"] < short_threshold and not is_perimeter(ed):
                filtered_out.append({**ed, "reason": "short_non_perimeter_transition"})
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

    semantic = [ed for ed in semantic if ed["len"] >= final_min_len]
    return semantic, filtered_out, base


def _filter_staircase(edges, base):
    """
    Drop edges that form evenly-spaced runs (stair treads, hatching, repeated
    fixtures). Detection: ≥4 positions with the same (rounded-center,
    rounded-length) bucket AND evenly spaced.

    Ported from navvix_v13.applier._filter_staircase. v18 dropped this; the
    sample comparison showed stair treads getting dimensioned (400/400/120/120)
    so it's restored.
    """
    if not edges:
        return [], []

    match_tol   = max(20.0, base * 0.020)
    spacing_var = 0.35
    min_treads  = 4

    span_positions: dict[tuple, list[float]] = {}
    for ed in edges:
        center = (ed["a"] + ed["b"]) / 2
        key = (ed["ori"],
               round(center / match_tol) * match_tol,
               round(ed["len"] / match_tol) * match_tol)
        span_positions.setdefault(key, []).append(ed["c"])

    stair_keys: set[tuple] = set()
    for key, positions in span_positions.items():
        if len(positions) < min_treads:
            continue
        ps = sorted(positions)
        cur_run = 2
        for i in range(1, len(ps) - 1):
            d_prev = ps[i] - ps[i - 1]
            d_next = ps[i + 1] - ps[i]
            if d_prev > 0 and abs(d_next - d_prev) / d_prev <= spacing_var:
                cur_run += 1
                if cur_run >= min_treads:
                    stair_keys.add(key)
                    break
            else:
                cur_run = 2

    if not stair_keys:
        return edges, []

    kept, dropped = [], []
    for ed in edges:
        center = (ed["a"] + ed["b"]) / 2
        key = (ed["ori"],
               round(center / match_tol) * match_tol,
               round(ed["len"] / match_tol) * match_tol)
        if key in stair_keys:
            dropped.append({**ed, "reason": "staircase_run"})
        else:
            kept.append(ed)
    return kept, dropped


def _dedup_near_identical(edges, base):
    """
    Drop near-duplicate edges that would produce stacked dimensions. Two
    edges are duplicates if same orientation, perpendicular axis (c) within
    tolerance, and parallel-axis span overlap ≥ 80%. Keep the longer.

    Without this the sample comparison shows 4 stacked '280' rows.
    """
    if not edges:
        return [], []

    pos_tol = max(15.0, base * 0.015)
    overlap_thresh = 0.80

    sorted_edges = sorted(edges, key=lambda e: e["len"], reverse=True)
    kept: list[dict] = []
    dropped: list[dict] = []

    for ed in sorted_edges:
        is_dup = False
        for k in kept:
            if k["ori"] != ed["ori"]:
                continue
            if abs(k["c"] - ed["c"]) > pos_tol:
                continue
            lo = max(min(k["a"], k["b"]), min(ed["a"], ed["b"]))
            hi = min(max(k["a"], k["b"]), max(ed["a"], ed["b"]))
            overlap = max(0.0, hi - lo)
            shorter = min(k["len"], ed["len"])
            if shorter > 0 and overlap / shorter >= overlap_thresh:
                is_dup = True
                break
        if is_dup:
            dropped.append({**ed, "reason": "near_duplicate_of_longer"})
        else:
            kept.append(ed)
    return kept, dropped


def _select_significant(edges, arch_bbox):
    """
    Sparser selection — sample has ~19 dims; v18 was producing 16-35 with
    many noise dims. Keep all perimeter-band edges; for interior, require
    length ≥ base * 0.15. Cap total at perimeter / 280 (sample density).
    """
    xlo, ylo, xhi, yhi = arch_bbox
    base = max(1.0, min(xhi - xlo, yhi - ylo))
    perim_band = max(90.0, base * 0.030)
    interior_min = base * 0.15

    def is_perimeter(ed):
        if ed["ori"] == "H":
            return min(abs(ed["c"] - ylo), abs(ed["c"] - yhi)) <= perim_band
        return min(abs(ed["c"] - xlo), abs(ed["c"] - xhi)) <= perim_band

    kept = []
    dropped = []
    for ed in edges:
        if is_perimeter(ed):
            kept.append({**ed, "_perim": True})
        elif ed["len"] >= interior_min:
            kept.append({**ed, "_perim": False})
        else:
            dropped.append({**ed, "reason": "interior_below_significance"})

    apt_perim = 2 * ((xhi - xlo) + (yhi - ylo))
    target = max(8, min(28, int(apt_perim / 280)))
    if len(kept) > target:
        # Importance: perimeter+long first; then long interior.
        def score(ed):
            return (1 if ed["_perim"] else 0) * 1e6 + ed["len"]
        kept.sort(key=score, reverse=True)
        for ed in kept[target:]:
            dropped.append({**ed, "reason": "over_target_density"})
        kept = kept[:target]

    return [{k: v for k, v in ed.items() if k != "_perim"} for ed in kept], dropped


def _classify_levels(edges, arch_bbox):
    """
    Three-level dimension hierarchy (req #4):
      LEVEL 1 (local):    short interior walls
      LEVEL 2 (group):    medium walls / room boundaries
      LEVEL 3 (overall):  perimeter spans + longest walls
    """
    xlo, ylo, xhi, yhi = arch_bbox
    base = max(1.0, min(xhi - xlo, yhi - ylo))
    perimeter_band = max(90.0, base * 0.030)

    def is_perimeter(ed):
        if ed["ori"] == "H":
            return min(abs(ed["c"] - ylo), abs(ed["c"] - yhi)) <= perimeter_band
        return min(abs(ed["c"] - xlo), abs(ed["c"] - xhi)) <= perimeter_band

    if not edges:
        return []

    lengths = sorted(ed["len"] for ed in edges)
    q1 = lengths[len(lengths) // 4] if len(lengths) >= 4 else lengths[0]
    q3 = lengths[(len(lengths) * 3) // 4] if len(lengths) >= 4 else lengths[-1]

    classified = []
    for ed in edges:
        L = ed["len"]
        if is_perimeter(ed) and L >= q3:
            level = 3
        elif L >= q3:
            level = 3
        elif L >= q1:
            level = 2
        else:
            level = 1
        classified.append({**ed, "level": level})
    return classified


# ═══════════════════════════════════════════════════════════════════════════
# Dimstyle: filled triangle arrows (req #5)
# ═══════════════════════════════════════════════════════════════════════════

def _setup_dimstyle(doc, base, style="ISO-25"):
    doc.units = units.MM
    if style not in doc.dimstyles:
        doc.dimstyles.new(style)
    ds = doc.dimstyles.get(style)

    # Scale dim text/arrows with drawing size (legible at any scale).
    dim_txt = max(16.0, base * 0.020)
    dim_asz = max(20.0, base * 0.012)

    settings = {
        "dimtxt":   dim_txt,
        "dimasz":   dim_asz,
        "dimgap":   max(7.0,  base * 0.005),
        "dimexe":   max(4.0,  base * 0.003),
        "dimexo":   max(3.0,  base * 0.002),
        "dimtad":   1,  # text above the dim line
        "dimjust":  0,  # centered
        "dimtih":   0,  # text aligned with dim line (horiz)
        "dimtoh":   0,
        "dimblk":   "",  # default → ACAD "_CLOSED_FILLED" filled triangle
        "dimblk1":  "",
        "dimblk2":  "",
        "dimsah":   0,  # use single arrow block both ends
        "dimscale": 1.0,
        "dimlunit": 2,
        "dimzin":   8,
        "dimdec":   0,
        # Fit options: allow text/arrow movement when space is tight.
        "dimtix":   0,
        "dimtofl":  1,
        "dimtmove": 1,
        "dimatfit": 3,
    }
    for k, v in settings.items():
        try:
            setattr(ds.dxf, k, v)
        except Exception:
            pass
    return style, dim_txt, dim_asz


# ═══════════════════════════════════════════════════════════════════════════
# Placement with 3-level hierarchy + basic collision avoidance (req #4, #6, #7)
# ═══════════════════════════════════════════════════════════════════════════

def _place_dimensions(doc, classified_edges, arch_bbox, base, dim_txt, dim_asz):
    xlo, ylo, xhi, yhi = arch_bbox
    msp = doc.modelspace()
    layer = "V18_DIMS"
    if layer not in doc.layers:
        doc.layers.new(layer, dxfattribs={"color": 7})
    style = "ISO-25"

    # Offsets per level (scale with drawing).
    off1 = max(40.0,  base * 0.018)
    off2 = max(80.0,  base * 0.036)
    off3 = max(120.0, base * 0.054)
    offsets = {1: off1, 2: off2, 3: off3}

    # Track placed ranges per (axis, side, level) for basic collision check.
    # axis: 'H' (dim line horizontal, on top/bottom) or 'V' (left/right).
    # side: +1 or -1.
    # level: 1/2/3.
    # range: (lo, hi) along the dim line's parallel axis.
    placed = {}  # key -> list[(lo, hi)]
    created = []

    def overlaps(rng, existing):
        lo, hi = rng
        for elo, ehi in existing:
            if not (hi < elo - dim_asz or lo > ehi + dim_asz):
                return True
        return False

    cx, cy = (xlo + xhi) / 2, (ylo + yhi) / 2

    for ed in classified_edges:
        ori = ed["ori"]
        a, b, c = ed["a"], ed["b"], ed["c"]
        level = ed["level"]

        if ori == "H":
            side = 1 if c >= cy else -1
            # Try the edge's own level first; bump up if collision.
            tried = []
            chosen = None
            for try_level in (level, min(3, level + 1), max(1, level - 1)):
                if try_level in tried:
                    continue
                tried.append(try_level)
                key = ("H", side, try_level)
                existing = placed.get(key, [])
                rng = (min(a, b), max(a, b))
                if not overlaps(rng, existing):
                    chosen = try_level
                    placed.setdefault(key, []).append(rng)
                    break
            if chosen is None:
                # Force at the highest level even with collision (rare).
                chosen = 3
                placed.setdefault(("H", side, 3), []).append((min(a, b), max(a, b)))

            off = offsets[chosen]
            basept = ((a + b) / 2, c + side * off)
            try:
                d = msp.add_linear_dim(
                    base=basept, p1=(a, c), p2=(b, c), angle=0,
                    dimstyle=style, dxfattribs={"layer": layer},
                )
                d.render()
                created.append({
                    "ori": "H", "level": chosen,
                    "p1": [a, c], "p2": [b, c], "base": list(basept),
                    "length": ed["len"],
                })
            except Exception as ex:
                created.append({"error": str(ex), "edge": ed})

        else:  # V
            side = 1 if c >= cx else -1
            tried = []
            chosen = None
            for try_level in (level, min(3, level + 1), max(1, level - 1)):
                if try_level in tried:
                    continue
                tried.append(try_level)
                key = ("V", side, try_level)
                existing = placed.get(key, [])
                rng = (min(a, b), max(a, b))
                if not overlaps(rng, existing):
                    chosen = try_level
                    placed.setdefault(key, []).append(rng)
                    break
            if chosen is None:
                chosen = 3
                placed.setdefault(("V", side, 3), []).append((min(a, b), max(a, b)))

            off = offsets[chosen]
            basept = (c + side * off, (a + b) / 2)
            try:
                d = msp.add_linear_dim(
                    base=basept, p1=(c, a), p2=(c, b), angle=90,
                    dimstyle=style, dxfattribs={"layer": layer},
                )
                d.render()
                created.append({
                    "ori": "V", "level": chosen,
                    "p1": [c, a], "p2": [c, b], "base": list(basept),
                    "length": ed["len"],
                })
            except Exception as ex:
                created.append({"error": str(ex), "edge": ed})

    return created, offsets


# ═══════════════════════════════════════════════════════════════════════════
# Validation (req #10)
# ═══════════════════════════════════════════════════════════════════════════

def _validate(created, arch_bbox, offsets):
    xlo, ylo, xhi, yhi = arch_bbox
    max_off = offsets[3] * 1.6
    bounds = (xlo - max_off, ylo - max_off, xhi + max_off, yhi + max_off)

    errors = []
    good = [c for c in created if "error" not in c]
    bad  = [c for c in created if "error" in c]
    if bad:
        errors.append({"kind": "placement_errors", "count": len(bad)})

    out_of_bounds = 0
    for c in good:
        bx, by = c["base"]
        if not (bounds[0] <= bx <= bounds[2] and bounds[1] <= by <= bounds[3]):
            out_of_bounds += 1
    if out_of_bounds:
        errors.append({"kind": "out_of_bounds", "count": out_of_bounds})

    # Duplicate detection (same ori + endpoints rounded to integer units).
    seen = set()
    dups = 0
    for c in good:
        key = (c["ori"], round(c["p1"][0]), round(c["p1"][1]),
               round(c["p2"][0]), round(c["p2"][1]))
        if key in seen:
            dups += 1
        else:
            seen.add(key)
    if dups:
        errors.append({"kind": "duplicates", "count": dups})

    zero_len = sum(1 for c in good if c.get("length", 1) < 1)
    if zero_len:
        errors.append({"kind": "zero_length", "count": zero_len})

    return {"errors": errors, "valid": not errors, "kept": len(good)}


# ═══════════════════════════════════════════════════════════════════════════
# Preview rendering (req #2 tight crop, #5 filled arrows, #9 legible style)
# ═══════════════════════════════════════════════════════════════════════════

def _preview_geometry(doc):
    segments = []
    dims = []
    for e in doc.modelspace():
        t = e.dxftype()
        layer = getattr(e.dxf, "layer", "")
        try:
            if t == "LINE":
                a, b = e.dxf.start, e.dxf.end
                if math.hypot(b.x - a.x, b.y - a.y) > 1e-6:
                    segments.append((float(a.x), float(a.y), float(b.x), float(b.y), layer))
            elif t == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points()]
                if e.closed and pts:
                    pts.append(pts[0])
                for a, b in zip(pts, pts[1:]):
                    if math.hypot(b[0] - a[0], b[1] - a[1]) > 1e-6:
                        segments.append((float(a[0]), float(a[1]), float(b[0]), float(b[1]), layer))
            elif t == "POLYLINE":
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                if e.is_closed and pts:
                    pts.append(pts[0])
                for a, b in zip(pts, pts[1:]):
                    if math.hypot(b[0] - a[0], b[1] - a[1]) > 1e-6:
                        segments.append((float(a[0]), float(a[1]), float(b[0]), float(b[1]), layer))
            elif t == "DIMENSION":
                p1 = e.dxf.defpoint2
                p2 = e.dxf.defpoint3
                base = e.dxf.defpoint
                dims.append({
                    "p1":    (float(p1.x), float(p1.y)),
                    "p2":    (float(p2.x), float(p2.y)),
                    "base":  (float(base.x), float(base.y)),
                    "angle": float(getattr(e.dxf, "angle", 0) or 0),
                })
        except Exception:
            pass
    return segments, dims


def _filled_arrow(ax, tip, direction, size):
    x, y = tip
    dx, dy = direction
    n = math.hypot(dx, dy)
    if n == 0:
        return
    dx, dy = dx / n, dy / n
    px, py = -dy, dx
    pts = np.array([
        [x, y],
        [x - dx * size + px * size * 0.32, y - dy * size + py * size * 0.32],
        [x - dx * size - px * size * 0.32, y - dy * size - py * size * 0.32],
    ])
    ax.add_patch(Polygon(pts, closed=True, facecolor="black", edgecolor="black", linewidth=0.4))


def _dim_value(d):
    p1, p2 = d["p1"], d["p2"]
    is_h = abs(d["angle"]) < 45 or abs(d["angle"] - 180) < 45
    return str(int(round(abs(p2[0] - p1[0]) if is_h else abs(p2[1] - p1[1]))))


def _render_preview(dxf_path, png_path, pdf_path, arch_bbox, base, offsets):
    doc = ezdxf.readfile(str(dxf_path))
    segments, dims = _preview_geometry(doc)
    xlo, ylo, xhi, yhi = arch_bbox

    # Tight crop: small fixed pad relative to base, NOT bbox-percentage.
    # Just enough room for the level-3 offset + a margin.
    pad = offsets[3] + max(40.0, base * 0.020)
    arrow_size = max(20.0, base * 0.014)
    wall_lw    = 1.4
    dim_lw     = 0.55
    ext_lw     = 0.40
    text_off   = max(14.0, base * 0.008)
    font_size  = 9.0

    fig, ax = plt.subplots(figsize=(14, 10), facecolor="white")

    for x1, y1, x2, y2, layer in segments:
        lu = layer.upper()
        if "DIM" in lu or "V17" in lu or "V18" in lu:
            continue
        # Only draw segments touching the tight bbox.
        if max(x1, x2) < xlo - pad or min(x1, x2) > xhi + pad:
            continue
        if max(y1, y2) < ylo - pad or min(y1, y2) > yhi + pad:
            continue
        ax.plot([x1, x2], [y1, y2], color="black", linewidth=wall_lw, solid_capstyle="round")

    cx, cy = (xlo + xhi) / 2, (ylo + yhi) / 2

    for d in dims:
        p1, p2, bp = d["p1"], d["p2"], d["base"]
        if not (xlo - pad <= bp[0] <= xhi + pad and ylo - pad <= bp[1] <= yhi + pad):
            continue
        is_h = abs(d["angle"]) < 45 or abs(d["angle"] - 180) < 45
        text = _dim_value(d)

        if is_h:
            xa, xb = sorted([p1[0], p2[0]])
            y = bp[1]
            ax.plot([xa, xb], [y, y], color="black", linewidth=dim_lw)
            ax.plot([xa, xa], [p1[1], y], color="black", linewidth=ext_lw)
            ax.plot([xb, xb], [p2[1], y], color="black", linewidth=ext_lw)
            _filled_arrow(ax, (xa, y), (1, 0), arrow_size)
            _filled_arrow(ax, (xb, y), (-1, 0), arrow_size)

            # Centered text by default; bump outside if span too small.
            span = xb - xa
            text_w = len(text) * font_size * 0.65 + arrow_size * 1.5
            if span < text_w:
                tx = xb + text_w * 0.5
                ha = "left"
            else:
                tx = (xa + xb) / 2
                ha = "center"
            side = 1 if y >= cy else -1
            ax.text(tx, y + side * text_off, text,
                    fontsize=font_size, ha=ha, va="center",
                    color="black")
        else:
            ya, yb = sorted([p1[1], p2[1]])
            x = bp[0]
            ax.plot([x, x], [ya, yb], color="black", linewidth=dim_lw)
            ax.plot([p1[0], x], [ya, ya], color="black", linewidth=ext_lw)
            ax.plot([p2[0], x], [yb, yb], color="black", linewidth=ext_lw)
            _filled_arrow(ax, (x, ya), (0, 1), arrow_size)
            _filled_arrow(ax, (x, yb), (0, -1), arrow_size)

            span = yb - ya
            text_w = len(text) * font_size * 0.65 + arrow_size * 1.5
            if span < text_w:
                ty = yb + text_w * 0.5
                va = "bottom"
            else:
                ty = (ya + yb) / 2
                va = "center"
            side = 1 if x >= cx else -1
            ax.text(x + side * text_off, ty, text,
                    fontsize=font_size, ha="center", va=va,
                    rotation=90, color="black")

    ax.set_xlim(xlo - pad, xhi + pad)
    ax.set_ylim(ylo - pad, yhi + pad)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout(pad=0.2)
    fig.savefig(png_path, dpi=280, bbox_inches="tight", facecolor="white")
    fig.savefig(pdf_path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════
# Top-level pipeline
# ═══════════════════════════════════════════════════════════════════════════

def process(input_dxf, output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    isolated_dxf    = output_dir / "v18_isolated.dxf"
    dimensioned_dxf = output_dir / "v18_dimensioned.dxf"
    preview_png     = output_dir / "v18_preview.png"
    preview_pdf     = output_dir / "v18_preview.pdf"
    report_path     = output_dir / "v18_report.json"

    iso_report = isolate_main_plan(input_dxf, isolated_dxf)
    arch_bbox = iso_report["arch_bbox"]

    doc = ezdxf.readfile(str(isolated_dxf))
    raw_edges = _extract_edges(doc)
    semantic, filtered_short, base = _semantic_merge(raw_edges, arch_bbox)
    # v18 iteration 2: sample-driven filters
    semantic, stair_dropped = _filter_staircase(semantic, base)
    semantic, dup_dropped   = _dedup_near_identical(semantic, base)
    semantic, sparse_dropped = _select_significant(semantic, arch_bbox)
    filtered_out = filtered_short + stair_dropped + dup_dropped + sparse_dropped
    classified = _classify_levels(semantic, arch_bbox)

    style, dim_txt, dim_asz = _setup_dimstyle(doc, base)
    created, offsets = _place_dimensions(doc, classified, arch_bbox, base, dim_txt, dim_asz)
    doc.saveas(str(dimensioned_dxf))

    _render_preview(dimensioned_dxf, preview_png, preview_pdf, arch_bbox, base, offsets)

    validation = _validate(created, arch_bbox, offsets)

    level_counts = {1: 0, 2: 0, 3: 0}
    for c in created:
        if "level" in c:
            level_counts[c["level"]] += 1

    h_count = sum(1 for c in created if c.get("ori") == "H")
    v_count = sum(1 for c in created if c.get("ori") == "V")

    report = {
        "input":                  str(input_dxf),
        "isolated_dxf":           str(isolated_dxf),
        "dimensioned_dxf":        str(dimensioned_dxf),
        "preview_png":            str(preview_png),
        "preview_pdf":            str(preview_pdf),
        "isolation":              iso_report,
        "raw_edges":              len(raw_edges),
        "semantic_edges":         len(semantic),
        "filtered_out_edges":     len(filtered_out),
        "filter_breakdown": {
            "short_non_perimeter": len(filtered_short),
            "staircase_runs":      len(stair_dropped),
            "near_duplicates":     len(dup_dropped),
            "below_significance":  len(sparse_dropped),
        },
        "dimensions_created":     validation["kept"],
        "level_counts":           level_counts,
        "x_axes":                 h_count,
        "y_axes":                 v_count,
        "base_dim":               base,
        "offsets":                offsets,
        "style": {
            "dimstyle": "ISO-25",
            "dimtxt":   dim_txt,
            "dimasz":   dim_asz,
        },
        "validation":             validation,
        "rule":                   "v18: hard-reject frames/tables, tight crop, 3-level hierarchy, filled arrows",
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description="navvix v18 — architectural-quality dimension engine")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    result = process(args.input, args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
