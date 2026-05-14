"""
navvix_v12.split
----------------
Detect and split a multi-drawing DXF (e.g. an apartment-sheet contact sheet
with N floor plans on one page) into per-drawing DXFs.

The detection rule (inherited from scripts/split_drawings.py):
  1. Outer sheet boundaries are large LWPOLYLINE rectangles on layer "0".
  2. Within each sheet, the inner floor-plan boundary is the largest closed
     polyline on a drawing layer (default: "BAR-GAL"); fall back to the
     sheet bounds when no inner polygon is found.
  3. Entities whose centroid falls inside the inner boundary (plus a small
     pad) are copied to a per-drawing DXF.

Public entry point:
  split_drawing(input_dxf, output_dir) -> list[tuple[Path, str]]

Returns the list of (path, label) pairs for the produced files. An empty
list signals "no split applicable" (single drawing, or detection failed) —
callers should fall back to single-drawing processing on the original file.
"""

from __future__ import annotations

from pathlib import Path

import ezdxf


# ── geometry helpers ─────────────────────────────────────────────────────────

def _entity_centroid(e):
    t = e.dxftype()
    try:
        if t == "LINE":
            return (e.dxf.start.x + e.dxf.end.x) / 2, (e.dxf.start.y + e.dxf.end.y) / 2
        if t == "LWPOLYLINE":
            pts = list(e.get_points())
            if pts:
                return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        if t in ("ARC", "CIRCLE"):
            return e.dxf.center.x, e.dxf.center.y
        if t == "DIMENSION":
            dp2, dp3 = e.dxf.defpoint2, e.dxf.defpoint3
            return (dp2.x + dp3.x) / 2, (dp2.y + dp3.y) / 2
        if t in ("TEXT", "MTEXT", "INSERT", "POINT"):
            p = e.dxf.insert
            return p.x, p.y
        if t == "SPLINE":
            cpts = list(e.control_points)
            if cpts:
                return sum(p[0] for p in cpts) / len(cpts), sum(p[1] for p in cpts) / len(cpts)
    except Exception:
        pass
    return None


def _poly_bbox(p):
    pts = [(pt[0], pt[1]) for pt in p.get_points()]
    xs = [pt[0] for pt in pts]
    ys = [pt[1] for pt in pts]
    return min(xs), max(xs), min(ys), max(ys)


# ── sheet + inner-boundary detection ─────────────────────────────────────────

def _find_outer_boundaries(doc, min_width=2000, min_height=3000):
    bboxes = []
    for e in doc.modelspace():
        if e.dxftype() != "LWPOLYLINE" or e.dxf.layer != "0":
            continue
        pts = list(e.get_points())
        if len(pts) < 3:
            continue
        x0, x1, y0, y1 = _poly_bbox(e)
        w, h = x1 - x0, y1 - y0
        if w >= min_width and h >= min_height:
            bboxes.append((x0, x1, y0, y1))
    bboxes.sort(key=lambda b: b[0])
    return bboxes


def _find_floorplan_boundary(all_entities, sheet_x0, sheet_x1, sheet_y0, sheet_y1,
                             drawing_layers=("BAR-GAL",), min_pts=4):
    best_area = 0
    best_bbox = (sheet_x0, sheet_x1, sheet_y0, sheet_y1)

    for e in all_entities:
        if e.dxftype() != "LWPOLYLINE":
            continue
        if e.dxf.layer not in drawing_layers:
            continue
        pts = list(e.get_points())
        if len(pts) < min_pts:
            continue
        x0, x1, y0, y1 = _poly_bbox(e)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if not (sheet_x0 <= cx <= sheet_x1 and sheet_y0 <= cy <= sheet_y1):
            continue
        area = (x1 - x0) * (y1 - y0)
        if area > best_area:
            best_area = area
            best_bbox = (x0, x1, y0, y1)

    return best_bbox


# ── public API ───────────────────────────────────────────────────────────────

def detect_sheets(input_dxf) -> int:
    """
    Cheap pre-check: number of apartment sheets in the DXF without doing the
    full per-drawing copy. Returns 0 if the file can't be read.
    """
    try:
        doc = ezdxf.readfile(str(input_dxf))
    except Exception:
        return 0
    return len(_find_outer_boundaries(doc))


def split_drawing(input_dxf, output_dir,
                  drawing_layers=("BAR-GAL",)) -> list[tuple[Path, str]]:
    """
    Split a multi-drawing DXF into per-drawing DXFs written to output_dir.

    Returns
    -------
    list of (path, label) tuples. Empty list means no split was applicable
    (single drawing, or detection produced ≤ 1 sheet) — caller should
    process the original file as-is.
    """
    input_dxf = Path(input_dxf)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    doc = ezdxf.readfile(str(input_dxf))
    all_entities = list(doc.modelspace())

    sheets = _find_outer_boundaries(doc)
    if len(sheets) < 2:
        return []

    written: list[tuple[Path, str]] = []
    n = len(sheets)
    stem = input_dxf.stem
    pad = 20

    for i, (sx0, sx1, sy0, sy1) in enumerate(sheets):
        fp_x0, fp_x1, fp_y0, fp_y1 = _find_floorplan_boundary(
            all_entities, sx0, sx1, sy0, sy1, drawing_layers)

        out_doc = ezdxf.new(dxfversion=doc.dxfversion)
        for ds in doc.dimstyles:
            try:
                out_doc.dimstyles.new(ds.dxf.name,
                    dxfattribs=dict(ds.dxf.all_existing_dxf_attribs()))
            except Exception:
                pass
        for lyr in doc.layers:
            try:
                out_doc.layers.new(lyr.dxf.name, dxfattribs={
                    "color":    lyr.dxf.get("color", 7),
                    "linetype": lyr.dxf.get("linetype", "Continuous"),
                })
            except Exception:
                pass

        out_msp = out_doc.modelspace()
        for e in all_entities:
            c = _entity_centroid(e)
            if c is None:
                continue
            cx, cy = c
            if (fp_x0 - pad) <= cx <= (fp_x1 + pad) and \
               (fp_y0 - pad) <= cy <= (fp_y1 + pad):
                try:
                    out_msp.add_entity(e.copy())
                except Exception:
                    pass

        idx = i + 1
        label = f"apt {idx}/{n}"
        out_path = output_dir / f"{stem}_apt{idx:02d}.dxf"
        out_doc.saveas(str(out_path))
        written.append((out_path, label))

    return written
