"""
navvix_v13.learner
------------------
Learn dimension placement patterns from training DXF files that already
contain dimensions.  Produces a style_model.json consumed by applier.py.

Extraction per file
-------------------
1. Find drawing bbox from geometry (percentile-clipped, avoids outliers)
2. Compute base_dim = min(W, H) of that bbox
3. Extract every DIMENSION entity and classify as:
      top / bottom / left / right  (external, outside bbox)
      internal                     (dim line inside bbox)
4. For external dims: record offset_ratio = raw_offset / base_dim
   Minimum offset per zone → chain; maximum → overall
5. For style: read dimasz/dimtxt/… from the doc's dimstyles, normalise
6. Aggregate medians across all files → model.json
"""

from __future__ import annotations

import json, math, statistics
from pathlib import Path

import ezdxf
import numpy as np

# ── re-use geometry helpers from v12 ────────────────────────────────────────
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from navvix_v12.__main__ import entity_pts, bb, GEOM, extract_segments


# ═══════════════════════════════════════════════════════════════════════════
# Internal helpers
# ═══════════════════════════════════════════════════════════════════════════

def _drawing_bbox(doc):
    """
    Return (bbox, base_dim) from geometry entities only, filtering outliers
    with a 5-95th-percentile clip.  Returns (None, None) on failure.
    """
    geo_pts = []
    for e in doc.modelspace():
        if e.dxftype() in GEOM:
            geo_pts.extend(entity_pts(e))

    if len(geo_pts) < 4:
        return None, None

    xs = [p[0] for p in geo_pts]
    ys = [p[1] for p in geo_pts]
    xlo, xhi = np.percentile(xs, [5, 95])
    ylo, yhi = np.percentile(ys, [5, 95])
    core = [(x, y) for x, y in geo_pts if xlo <= x <= xhi and ylo <= y <= yhi]
    if not core:
        core = geo_pts

    bbox = bb(core)
    W = bbox[2] - bbox[0];  H = bbox[3] - bbox[1]
    if W < 100 or H < 100:
        return None, None
    return bbox, min(W, H)


def _extract_dims(doc, drawing_bbox, base_dim):
    """
    Walk modelspace, classify every DIMENSION entity relative to drawing_bbox.
    Returns a list of dicts with zone, type, offset_ratio, L_ratio.
    """
    minx, miny, maxx, maxy = drawing_bbox
    W = max(1, maxx - minx);  H = max(1, maxy - miny)

    results = []
    for e in doc.modelspace():
        if e.dxftype() != "DIMENSION":
            continue
        try:
            p1   = (e.dxf.defpoint2.x, e.dxf.defpoint2.y)
            p2   = (e.dxf.defpoint3.x, e.dxf.defpoint3.y)
            base = (e.dxf.defpoint.x,  e.dxf.defpoint.y)
            ang  = float(getattr(e.dxf, "angle", 0) or 0)
        except Exception:
            continue

        is_h = abs(ang % 180) < 45

        if is_h:
            L = abs(p2[0] - p1[0])
            if L < 50:
                continue
            dim_pos  = base[1]
            wall_pos = (p1[1] + p2[1]) / 2   # actual wall Y
            L_ratio  = L / W
            if   dim_pos > maxy + 10:  zone = "top";      offset = dim_pos - maxy
            elif dim_pos < miny - 10:  zone = "bottom";   offset = miny - dim_pos
            elif miny <= dim_pos <= maxy: zone = "internal"; offset = dim_pos - miny
            else: continue
        else:
            L = abs(p2[1] - p1[1])
            if L < 50:
                continue
            dim_pos  = base[0]
            wall_pos = (p1[0] + p2[0]) / 2   # actual wall X
            L_ratio  = L / H
            if   dim_pos > maxx + 10:  zone = "right";    offset = dim_pos - maxx
            elif dim_pos < minx - 10:  zone = "left";     offset = minx - dim_pos
            elif minx <= dim_pos <= maxx: zone = "internal"; offset = dim_pos - minx
            else: continue

        # Offset of dim line FROM the wall (for internal dims → dim_offset_ratio)
        wall_offset = abs(dim_pos - wall_pos)

        results.append({
            "type":              "h" if is_h else "v",
            "zone":              zone,
            "L":                 L,
            "L_ratio":           L_ratio,
            "offset":            offset,
            "offset_ratio":      offset / max(1, base_dim),
            "wall_offset_ratio": wall_offset / max(1, base_dim),
        })
    return results


def _extract_style(doc, base_dim):
    """
    Return absolute dim-style values from the style actually used by
    dimension entities (prioritised) or the first usable style as fallback.
    """
    # Find which style names are actually used
    used_styles: set[str] = set()
    for e in doc.modelspace():
        if e.dxftype() == "DIMENSION":
            try:
                used_styles.add(e.dxf.dimstyle)
            except Exception:
                pass

    def _read_style(ds):
        vals = {
            "dimasz": getattr(ds.dxf, "dimasz", 0),
            "dimtxt": getattr(ds.dxf, "dimtxt", 0),
            "dimexo": getattr(ds.dxf, "dimexo", 0),
            "dimexe": getattr(ds.dxf, "dimexe", 0),
            "dimgap": getattr(ds.dxf, "dimgap", 0),
        }
        return vals if any(v > 0 for v in vals.values()) else None

    # Prefer the style(s) actually used by dims
    for ds in doc.dimstyles:
        try:
            if ds.dxf.name in used_styles:
                result = _read_style(ds)
                if result:
                    return result
        except Exception:
            pass

    # Fallback: first style with non-zero values
    for ds in doc.dimstyles:
        try:
            result = _read_style(ds)
            if result:
                return result
        except Exception:
            pass
    return {}


def _learn_one(dxf_path):
    """
    Process a single training DXF.
    Returns a data dict or None if the file cannot be used.
    """
    try:
        doc = ezdxf.readfile(str(dxf_path))
    except Exception:
        return None

    drawing_bbox, base_dim = _drawing_bbox(doc)
    if drawing_bbox is None:
        return None

    dims = _extract_dims(doc, drawing_bbox, base_dim)
    if len(dims) < 4:
        return None

    by_zone: dict[str, list] = {z: [] for z in ("top", "bottom", "left", "right", "internal")}
    for d in dims:
        by_zone[d["zone"]].append(d)

    ext_chain:   list[float] = []
    ext_overall: list[float] = []
    ext_min_L:   list[float] = []

    for zone_name in ("top", "bottom", "left", "right"):
        zd = by_zone[zone_name]
        if not zd:
            continue
        offsets = sorted(d["offset_ratio"] for d in zd)
        ext_chain.append(offsets[0])
        ext_overall.append(offsets[-1])
        ext_min_L.append(min(d["L_ratio"] for d in zd))

    # For internal dims: learn min_seg_len_ratio and dim_offset_ratio
    int_dims = by_zone["internal"]
    int_min_L_ratio    = min((d["L_ratio"]           for d in int_dims), default=0.04)
    int_wall_offsets   = [d["wall_offset_ratio"] for d in int_dims
                          if d.get("wall_offset_ratio", 0) > 0.001]
    int_dim_off_ratio  = statistics.median(int_wall_offsets) if int_wall_offsets else 0.06

    return {
        "file":             str(dxf_path),
        "base_dim":         base_dim,
        "dim_count":        len(dims),
        "ext_chain":        ext_chain,
        "ext_overall":      ext_overall,
        "ext_min_L":        ext_min_L,
        "int_count":        len(int_dims),
        "int_min_L_ratio":  int_min_L_ratio,
        "int_dim_off_ratio": int_dim_off_ratio,
        "style":            _extract_style(doc, base_dim),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Public API
# ═══════════════════════════════════════════════════════════════════════════

def learn(sample_paths: list, output_path=None):
    """
    Learn from all training DXFs.

    Parameters
    ----------
    sample_paths : list of Path / str
    output_path  : where to write style_model.json (optional)

    Returns
    -------
    (model_dict, report_dict)
    """
    results = []
    failed  = []

    for p in sample_paths:
        r = _learn_one(p)
        if r:
            results.append(r)
        else:
            failed.append(str(p))

    if not results:
        raise ValueError(
            f"No valid training files found (tried {len(sample_paths)}, "
            f"failed {len(failed)})"
        )

    def med(vals, default):
        return statistics.median(vals) if vals else default

    all_chain        = [v for r in results for v in r["ext_chain"]]
    all_overall      = [v for r in results for v in r["ext_overall"]]
    all_min_L        = [v for r in results for v in r["ext_min_L"]]
    int_counts       = [r["int_count"]          for r in results]
    int_min_L_ratios = [r["int_min_L_ratio"]    for r in results if r.get("int_count", 0) > 0]
    int_off_ratios   = [r["int_dim_off_ratio"]  for r in results if r.get("int_count", 0) > 0]

    sf = ["dimasz", "dimtxt", "dimexo", "dimexe", "dimgap"]
    sv = {
        f: [r["style"][f] for r in results if r["style"].get(f, 0) > 0]
        for f in sf
    }

    model = {
        "version":    "v13",
        "trained_on": len(results),
        "external": {
            "chain_offset_ratio":   med(all_chain,        0.08),
            "overall_offset_ratio": med(all_overall,      0.16),
            "min_len_ratio":        med(all_min_L,        0.04),
            "max_axes_x":           12,
            "max_axes_y":           12,
            # Segment-based applier (v13) parameters
            "min_seg_len_ratio":    med(int_min_L_ratios, 0.04),
            "dim_offset_ratio":     med(int_off_ratios,   0.06),
        },
        "internal": {
            "room": {
                "min_area_ratio": 0.015,
                "max_area_ratio": 0.18,
                "margin_ratio":   0.15,
                "enabled":        True,
            },
            "corridor": {
                "asp_threshold": 3.2,
                "margin_ratio":  0.15,
                "enabled":       True,
            },
            "living":    {"enabled": False},
            "service":   {"enabled": False},
            "perimeter": {"exclusion": 0.07, "enabled": False},
            "max_dims":  int(med(int_counts, 14)),
        },
        "style": {
            # Absolute values (same coordinate units as DXF model space)
            "dimasz": med(sv["dimasz"], 30.0),
            "dimtxt": med(sv["dimtxt"], 30.0),
            "dimexo": med(sv["dimexo"],  0.625),
            "dimexe": med(sv["dimexe"],  1.25),
            "dimgap": med(sv["dimgap"], 10.0),
        },
    }

    if output_path:
        Path(output_path).write_text(
            json.dumps(model, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    report = {
        "files_processed": len(results),
        "files_failed":    len(failed),
        "failed_files":    failed,
        "learned_patterns": {
            "chain_offset_ratio":   model["external"]["chain_offset_ratio"],
            "overall_offset_ratio": model["external"]["overall_offset_ratio"],
            "min_len_ratio":        model["external"]["min_len_ratio"],
            "min_seg_len_ratio":    model["external"]["min_seg_len_ratio"],
            "dim_offset_ratio":     model["external"]["dim_offset_ratio"],
            "avg_internal_dims":    med(int_counts, 0),
            "style":                model["style"],
        },
        "per_file": [
            {
                "file":     Path(r["file"]).name,
                "dims":     r["dim_count"],
                "base_dim": round(r["base_dim"], 1),
                "int_dims": r["int_count"],
            }
            for r in results
        ],
    }

    return model, report
