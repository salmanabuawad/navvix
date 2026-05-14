"""
navvix_v13.applier  —  Segment-based dimensioning
--------------------------------------------------
Replicates the reference style:
  • Exterior chains: boundary wall segments dimensioned OUTSIDE the floor plan
    (top / bottom / left / right chains, plus overall totals)
  • Interior dims: significant non-staircase interior walls dimensioned
    toward the drawing centre

Dimension style: ISO-25 (dimasz=30, dimtxt=30, dimexo=0.625, dimexe=1.25,
dimgap=10) — exact match to the reference training files.

v16 fixes (merged in):
  • Collinear touching edges are re-merged with relaxed tolerances after the
    initial dedup, catching segments that bucket into slightly-offset positions.
  • Short non-perimeter transition edges (stair treads, double-line jogs,
    fixture artifacts) are filtered before dimension placement.
"""

from __future__ import annotations

import json, math
from pathlib import Path

import ezdxf
import numpy as np
from ezdxf import units

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from navvix_v12.__main__ import extract_segments, clean_geometry


# ═══════════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def load_model(model_path) -> dict:
    return json.loads(Path(model_path).read_text(encoding="utf-8"))


def _add_dim(msp, p1, p2, base, angle, layer, style, min_len, created, kind):
    L = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    if L < min_len:
        return
    try:
        d = msp.add_linear_dim(
            base=base, p1=p1, p2=p2, angle=angle,
            dimstyle=style, dxfattribs={"layer": layer},
        )
        d.render()
        created.append({"kind": kind, "length": round(L, 1),
                        "p1": list(p1), "p2": list(p2)})
    except Exception:
        pass


def _merge_collinear(segments_1d: list[tuple[float, float]],
                     tol: float = 5.0) -> list[tuple[float, float]]:
    """Merge overlapping / touching 1-D intervals [a,b]."""
    if not segments_1d:
        return []
    segs = sorted((min(a, b), max(a, b)) for a, b in segments_1d)
    merged = [segs[0]]
    for a, b in segs[1:]:
        if a <= merged[-1][1] + tol:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _dedup_segments(segs_by_pos: dict[float, list[tuple[float, float]]],
                    pos_tol: float,
                    span_tol: float) -> dict[float, list[tuple[float, float]]]:
    """
    Merge segments that share almost the same position (Y for H, X for V)
    and have overlapping spans, returning the union spans per position bucket.
    """
    positions = sorted(segs_by_pos.keys())
    groups: list[list[float]] = []
    for p in positions:
        if groups and abs(p - groups[-1][-1]) <= pos_tol:
            groups[-1].append(p)
        else:
            groups.append([p])

    result: dict[float, list[tuple[float, float]]] = {}
    for grp in groups:
        rep = sum(grp) / len(grp)
        all_spans: list[tuple[float, float]] = []
        for p in grp:
            all_spans.extend(segs_by_pos[p])
        result[rep] = _merge_collinear(all_spans, span_tol)
    return result


def _max_consecutive_run(positions: list[float], spacing_var: float = 0.35) -> int:
    """Return the length of the longest evenly-spaced consecutive sub-run."""
    ps = sorted(positions)
    if len(ps) < 2:
        return 1
    max_run = 1
    cur_run = 2
    for i in range(1, len(ps) - 1):
        d_prev = ps[i]     - ps[i - 1]
        d_next = ps[i + 1] - ps[i]
        if d_prev > 0 and abs(d_next - d_prev) / d_prev <= spacing_var:
            cur_run += 1
            if cur_run > max_run:
                max_run = cur_run
        else:
            cur_run = 2
    return max_run


def _filter_staircase(segs_by_pos: dict[float, list[tuple[float, float]]],
                      span_tol: float,
                      min_treads: int = 4,
                      spacing_var: float = 0.35,
                      ) -> dict[float, list[tuple[float, float]]]:
    """
    Remove spans that form a staircase: any consecutive evenly-spaced run of
    ≥ min_treads positions with the same (bucketed) span shape.
    """
    from collections import defaultdict

    match_tol = max(span_tol * 4, 20.0)

    span_positions: dict[tuple, list[float]] = defaultdict(list)
    for pos, spans in segs_by_pos.items():
        for a, b in spans:
            center = (a + b) / 2
            length = b - a
            key = (round(center / match_tol) * match_tol,
                   round(length / match_tol) * match_tol)
            span_positions[key].append(pos)

    staircase_keys: set[tuple] = set()
    for key, positions in span_positions.items():
        if len(positions) < min_treads:
            continue
        if _max_consecutive_run(positions, spacing_var) >= min_treads:
            staircase_keys.add(key)

    if not staircase_keys:
        return segs_by_pos

    result: dict[float, list[tuple[float, float]]] = {}
    for pos, spans in segs_by_pos.items():
        kept = []
        for a, b in spans:
            center = (a + b) / 2
            length = b - a
            key = (round(center / match_tol) * match_tol,
                   round(length / match_tol) * match_tol)
            if key not in staircase_keys:
                kept.append((a, b))
        if kept:
            result[pos] = kept
    return result


# ── v16 fixes ────────────────────────────────────────────────────────────────

def _remerge_relaxed(segs_by_pos: dict[float, list[tuple[float, float]]],
                     pos_tol: float,
                     span_tol: float,
                     pos_factor: float = 1.5,
                     span_factor: float = 2.0,
                     ) -> tuple[dict[float, list[tuple[float, float]]], int]:
    """
    Second-pass collinear merge with relaxed tolerances.

    The initial `_dedup_segments` uses tight tolerances tuned for clean CAD
    geometry. Real-world drawings often have edges that *should* be one
    collinear segment but bucket into two slightly-offset positions (e.g.
    a wall line split by a 0.3-unit perpendicular drift), or touching edges
    separated by a sub-tolerance gap. This pass rescues them.

    Returns (merged_dict, collapsed_count) where collapsed_count is the number
    of position-buckets that got absorbed into a neighbour.
    """
    before = len(segs_by_pos)
    merged = _dedup_segments(segs_by_pos, pos_tol * pos_factor, span_tol * span_factor)
    return merged, max(0, before - len(merged))


def _filter_short_non_perimeter(segs_by_pos: dict[float, list[tuple[float, float]]],
                                perim_lo: float,
                                perim_hi: float,
                                min_keep: float,
                                ) -> tuple[dict[float, list[tuple[float, float]]], list[dict]]:
    """
    Drop spans shorter than `min_keep` whose position lies inside the interior
    band (not within the perimeter band defined by [perim_lo, perim_hi]).
    Perimeter-band positions are kept regardless of length (short chain
    segments on the boundary are legitimate).

    Returns (filtered_dict, dropped_records). Each dropped record matches the
    v16 report schema: {ori_axis_pos: pos, a, b, len, reason}.
    """
    kept: dict[float, list[tuple[float, float]]] = {}
    dropped: list[dict] = []
    for pos, spans in segs_by_pos.items():
        on_perimeter = (pos <= perim_lo) or (pos >= perim_hi)
        if on_perimeter:
            kept[pos] = spans
            continue
        keep_spans: list[tuple[float, float]] = []
        for a, b in spans:
            L = b - a
            if L < min_keep:
                dropped.append({
                    "pos": pos, "a": a, "b": b, "len": L,
                    "reason": "short_non_perimeter_transition_edge",
                })
            else:
                keep_spans.append((a, b))
        if keep_spans:
            kept[pos] = keep_spans
    return kept, dropped


# ═══════════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════════

def apply(isolated_dxf, output_dxf, model_path) -> dict:
    """
    Dimension isolated_dxf and save to output_dxf.
    Returns a generation report dict.
    """
    model   = load_model(model_path)
    doc     = ezdxf.readfile(str(isolated_dxf))

    clean_geometry(doc)
    segs = extract_segments(doc)
    if not segs:
        raise ValueError("No wall segments found after cleaning")

    # ── Bounding box ──────────────────────────────────────────────────────────
    all_xs = [c for s in segs for c in (s[0], s[2])]
    all_ys = [c for s in segs for c in (s[1], s[3])]
    xlo, xhi = np.percentile(all_xs, [2, 98])
    ylo, yhi = np.percentile(all_ys, [2, 98])
    segs_in = [
        s for s in segs
        if xlo - 50 <= s[0] <= xhi + 50 and xlo - 50 <= s[2] <= xhi + 50
        and ylo - 50 <= s[1] <= yhi + 50 and ylo - 50 <= s[3] <= yhi + 50
    ]
    if not segs_in:
        segs_in = segs

    xs2 = [c for s in segs_in for c in (s[0], s[2])]
    ys2 = [c for s in segs_in for c in (s[1], s[3])]
    minx, miny, maxx, maxy = min(xs2), min(ys2), max(xs2), max(ys2)
    w = maxx - minx;  h = maxy - miny
    base_dim   = min(w, h)
    center_x   = (minx + maxx) / 2
    center_y   = (miny + maxy) / 2
    pos_tol    = max(15, base_dim * 0.010)
    span_tol   = max(8,  base_dim * 0.005)

    # ── Dim parameters ────────────────────────────────────────────────────────
    em        = model["external"]
    sm        = model["style"]
    min_len   = max(50,  base_dim * em.get("min_seg_len_ratio",  0.04))
    # Offset of dim line from the wall (chain: outward, interior: toward centre)
    dim_off   = max(60,  base_dim * em.get("dim_offset_ratio",   0.06))
    # Overall dim offset: further out than the chain row
    overall_off = dim_off * 2.2

    # ── Dim style — ISO-25 match ───────────────────────────────────────────────
    doc.units = units.MM
    LAYER = "NAVVIX_V13_DIM"
    if LAYER not in doc.layers:
        doc.layers.new(LAYER, dxfattribs={"color": 7})

    sty = "NAVVIX_V13_DIM"
    ds  = doc.dimstyles.get(sty) if sty in doc.dimstyles else doc.dimstyles.new(sty)
    ds.dxf.dimblk   = ""
    ds.dxf.dimblk1  = ""
    ds.dxf.dimblk2  = ""
    ds.dxf.dimasz   = sm.get("dimasz",  30.0)
    ds.dxf.dimtxt   = sm.get("dimtxt",  30.0)
    ds.dxf.dimexo   = sm.get("dimexo",  0.625)
    ds.dxf.dimexe   = sm.get("dimexe",  1.25)
    ds.dxf.dimgap   = sm.get("dimgap",  10.0)
    ds.dxf.dimscale = 1.0
    ds.dxf.dimlunit = 2
    ds.dxf.dimzin   = 8
    ds.dxf.dimtad   = 1;  ds.dxf.dimjust = 0;  ds.dxf.dimdec = 0

    # ── Collect & deduplicate segments ────────────────────────────────────────
    h_by_y: dict[float, list[tuple[float, float]]] = {}
    v_by_x: dict[float, list[tuple[float, float]]] = {}

    for x1, y1, x2, y2, ori, _ in segs_in:
        if ori == "h":
            pos = (y1 + y2) / 2
            key = round(pos / pos_tol) * pos_tol
            h_by_y.setdefault(key, []).append((min(x1, x2), max(x1, x2)))
        else:
            pos = (x1 + x2) / 2
            key = round(pos / pos_tol) * pos_tol
            v_by_x.setdefault(key, []).append((min(y1, y2), max(y1, y2)))

    h_dedup = _dedup_segments(h_by_y, pos_tol, span_tol)
    v_dedup = _dedup_segments(v_by_x, pos_tol, span_tol)

    # ── v16 fix: relaxed-tolerance second-pass collinear merge ────────────────
    h_dedup, h_remerged = _remerge_relaxed(h_dedup, pos_tol, span_tol)
    v_dedup, v_remerged = _remerge_relaxed(v_dedup, pos_tol, span_tol)

    # ── Remove staircase / repetitive elements ────────────────────────────────
    h_before = set(h_dedup.keys())
    v_before = set(v_dedup.keys())
    h_dedup = _filter_staircase(h_dedup, span_tol)
    v_dedup = _filter_staircase(v_dedup, span_tol)

    # Track staircase zone: bounding box of positions removed by the filter
    h_removed = h_before - set(h_dedup.keys())
    v_removed = v_before - set(v_dedup.keys())
    stair_zone_y = (min(h_removed), max(h_removed)) if h_removed else None
    stair_zone_x = (min(v_removed), max(v_removed)) if v_removed else None

    msp     = doc.modelspace()
    created: list[dict] = []

    # ── Boundary detection buffers ────────────────────────────────────────────
    # H chains (top/bottom): large buffer to capture L-shaped floor plans whose
    # boundary walls can sit well inside the raw bbox (e.g. top at relY = 93%).
    # V chains (left/right): smaller buffer — plans are rarely L-shaped sideways.
    ext_buf_h = max(pos_tol * 10, base_dim * 0.22)
    ext_buf_v = max(pos_tol *  6, base_dim * 0.10)
    # Interior exclusion: use the larger of the two so no boundary wall leaks in
    ext_buf   = max(ext_buf_h, ext_buf_v)

    # ── v16 fix: filter short non-perimeter transition edges ──────────────────
    # Empirical threshold from the v16 reference: filtered edges max at ~115,
    # smallest legitimate interior dim ~137. Scale by base_dim for larger plans.
    transition_min_len = max(120.0, base_dim * 0.045)
    h_dedup, h_transition_dropped = _filter_short_non_perimeter(
        h_dedup, miny + ext_buf_h, maxy - ext_buf_h, transition_min_len)
    v_dedup, v_transition_dropped = _filter_short_non_perimeter(
        v_dedup, minx + ext_buf_v, maxx - ext_buf_v, transition_min_len)

    def _sig(spans, min_l):
        return [(a, b) for a, b in spans if (b - a) >= min_l]

    def _ext_chain_h(positions_spans, chain_y_anchor, above: bool, label: str):
        """
        Place exterior chain dims for all H boundary spans.
        Each span dims from its OWN wall Y, but all share a common chain line height.
        Spans with the same X range (double-line wall duplicates) are deduplicated
        by keeping only the one closest to the boundary.
        """
        sign    = 1 if above else -1
        chain_y = chain_y_anchor + sign * dim_off
        ovr_y   = chain_y_anchor + sign * overall_off

        # Collect (span_y, x1, x2) — keep outermost Y for duplicate X ranges
        seen_x: dict[tuple, float] = {}   # (x1_rounded, x2_rounded) -> best span_y
        span_list = []
        for span_y, spans in positions_spans:
            for x1, x2 in _sig(spans, min_len * 0.3):
                key = (round(x1 / span_tol) * round(span_tol),
                       round(x2 / span_tol) * round(span_tol))
                # Keep the Y that is most outward (max for top, min for bottom)
                existing = seen_x.get(key)
                if existing is None:
                    seen_x[key] = span_y
                    span_list.append([key, span_y, x1, x2])
                elif (above and span_y > existing) or (not above and span_y < existing):
                    # Replace with better Y
                    for item in span_list:
                        if item[0] == key:
                            item[1], item[2], item[3] = span_y, x1, x2
                    seen_x[key] = span_y

        all_xs = []
        for key, span_y, x1, x2 in span_list:
            _add_dim(msp, (x1, span_y), (x2, span_y),
                     ((x1+x2)/2, chain_y), 0, LAYER, sty, min_len*0.3, created, label)
            all_xs.extend([x1, x2])

        # Overall: full span extent at the anchor wall line
        if all_xs:
            x_lo, x_hi = min(all_xs), max(all_xs)
            if x_hi - x_lo > min_len:
                _add_dim(msp, (x_lo, chain_y_anchor), (x_hi, chain_y_anchor),
                         ((x_lo+x_hi)/2, ovr_y), 0, LAYER, sty, min_len, created, label + "_ovr")

    def _ext_chain_v(positions_spans, chain_x_anchor, right: bool, label: str):
        """
        Place exterior chain dims for all V boundary spans.
        Each span dims from its OWN wall X, all sharing a common chain line X.
        """
        sign    = 1 if right else -1
        chain_x = chain_x_anchor + sign * dim_off
        ovr_x   = chain_x_anchor + sign * overall_off

        seen_y: dict[tuple, float] = {}
        span_list = []
        for span_x, spans in positions_spans:
            for y1, y2 in _sig(spans, min_len * 0.3):
                key = (round(y1 / span_tol) * round(span_tol),
                       round(y2 / span_tol) * round(span_tol))
                existing = seen_y.get(key)
                if existing is None:
                    seen_y[key] = span_x
                    span_list.append([key, span_x, y1, y2])
                elif (not right and span_x < existing) or (right and span_x > existing):
                    for item in span_list:
                        if item[0] == key:
                            item[1], item[2], item[3] = span_x, y1, y2
                    seen_y[key] = span_x

        all_ys = []
        for key, span_x, y1, y2 in span_list:
            _add_dim(msp, (span_x, y1), (span_x, y2),
                     (chain_x, (y1+y2)/2), 90, LAYER, sty, min_len*0.3, created, label)
            all_ys.extend([y1, y2])

        if all_ys:
            y_lo, y_hi = min(all_ys), max(all_ys)
            if y_hi - y_lo > min_len:
                _add_dim(msp, (chain_x_anchor, y_lo), (chain_x_anchor, y_hi),
                         (ovr_x, (y_lo+y_hi)/2), 90, LAYER, sty, min_len, created, label + "_ovr")

    # ── Exterior H chains (top and bottom) ────────────────────────────────────
    # Collect ALL H positions within ext_buf of each boundary edge, then merge
    # their spans so an L-shaped top wall still contributes all its segments.
    top_h = [(sy, sp) for sy, sp in h_dedup.items() if sy >= maxy - ext_buf_h]
    bot_h = [(sy, sp) for sy, sp in h_dedup.items() if sy <= miny + ext_buf_h]

    if top_h:
        top_wall_y = max(sy for sy, _ in top_h)   # outermost Y (highest)
        _ext_chain_h(top_h, top_wall_y, above=True,  label="top_chain")

    if bot_h:
        bot_wall_y = min(sy for sy, _ in bot_h)   # outermost Y (lowest)
        _ext_chain_h(bot_h, bot_wall_y, above=False, label="bot_chain")

    # ── Exterior V chains (left and right) ────────────────────────────────────
    left_v  = [(sx, sp) for sx, sp in v_dedup.items() if sx <= minx + ext_buf_v]
    right_v = [(sx, sp) for sx, sp in v_dedup.items() if sx >= maxx - ext_buf_v]

    if left_v:
        left_wall_x = min(sx for sx, _ in left_v)  # outermost X (leftmost)
        _ext_chain_v(left_v, left_wall_x, right=False, label="left_chain")

    if right_v:
        right_wall_x = max(sx for sx, _ in right_v)  # outermost X (rightmost)
        _ext_chain_v(right_v, right_wall_x, right=True, label="right_chain")

    # ── Interior dims — collect candidates, then keep the N most significant ──
    int_min_len = max(min_len, base_dim * 0.15)   # higher bar for interior
    max_int     = model.get("internal", {}).get("max_dims", 12)

    # Secondary dedup with a coarser tolerance to merge wall-thickness pairs
    # (double lines ~30 units apart appear as two separate positions otherwise).
    int_pos_tol = max(pos_tol * 3, 40.0)
    h_int_dedup = _dedup_segments(h_dedup, int_pos_tol, span_tol)
    v_int_dedup = _dedup_segments(v_dedup, int_pos_tol, span_tol)

    int_candidates: list[tuple[float, tuple, tuple, tuple, str]] = []
    # (length, p1, p2, base, kind)

    for seg_y, spans in h_int_dedup.items():
        if seg_y >= maxy - ext_buf or seg_y <= miny + ext_buf:
            continue
        # Skip positions inside the staircase zone (already removed by filter,
        # but nearby positions that survived should also be excluded)
        if stair_zone_y and stair_zone_y[0] - int_pos_tol <= seg_y <= stair_zone_y[1] + int_pos_tol:
            continue
        off_y = -dim_off if seg_y > center_y else dim_off
        for x1, x2 in _sig(spans, int_min_len):
            L = x2 - x1
            base = ((x1 + x2) / 2, seg_y + off_y)
            int_candidates.append((L, (x1, seg_y), (x2, seg_y), base, "h_int"))

    for seg_x, spans in v_int_dedup.items():
        if seg_x <= minx + ext_buf or seg_x >= maxx - ext_buf:
            continue
        # Skip positions inside the staircase zone
        if stair_zone_x and stair_zone_x[0] - int_pos_tol <= seg_x <= stair_zone_x[1] + int_pos_tol:
            continue
        off_x = -dim_off if seg_x > center_x else dim_off
        for y1, y2 in _sig(spans, int_min_len):
            L = y2 - y1
            base = (seg_x + off_x, (y1 + y2) / 2)
            int_candidates.append((L, (seg_x, y1), (seg_x, y2), base, "v_int"))

    # Sort by length descending and take the most significant
    int_candidates.sort(key=lambda c: c[0], reverse=True)
    for L, p1, p2, base, kind in int_candidates[:max_int]:
        angle = 0 if kind == "h_int" else 90
        _add_dim(msp, p1, p2, base, angle, LAYER, sty, int_min_len * 0.5, created, kind)

    doc.saveas(str(output_dxf))

    by_kind: dict[str, int] = {}
    for d in created:
        by_kind[d["kind"]] = by_kind.get(d["kind"], 0) + 1

    h_count = sum(v for k, v in by_kind.items() if k.startswith("h_") or "chain" in k or "overall" in k)
    v_count = sum(v for k, v in by_kind.items() if k.startswith("v_") or "left" in k or "right" in k)

    return {
        "bbox":           [minx, miny, maxx, maxy],
        "x_axes":         len(h_dedup),
        "y_axes":         len(v_dedup),
        "segments":       len(segs_in),
        "dimensions":     created,
        "internal_count": len(created),
        "h_count":        by_kind.get("h_int", 0) + by_kind.get("top_chain", 0) + by_kind.get("bot_chain", 0),
        "v_count":        by_kind.get("v_int", 0) + by_kind.get("left_chain", 0) + by_kind.get("right_chain", 0),
        "by_kind":        by_kind,
        "model_version":  model.get("version", "v13"),
        "v16_fixes": {
            "relaxed_merge_collapsed":      h_remerged + v_remerged,
            "transition_filter_removed":    len(h_transition_dropped) + len(v_transition_dropped),
            "transition_filter_threshold":  transition_min_len,
        },
    }
