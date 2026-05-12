"""
split_drawings.py  —  Split a multi-apartment DXF into individual floor-plan files.

Usage:
    python split_drawings.py <input.dxf> <output_dir>

Strategy (two-level split):
  1. Find outer apartment sheet boundaries  (layer 0, large rectangles).
  2. Within each sheet, find the inner FLOOR PLAN boundary — the largest
     closed polygon on the BAR-GAL layer (or the dominant drawing layer).
  3. Extract only entities whose centroid falls inside the INNER boundary,
     discarding the title block and legend that surround the drawing.
"""

import sys
from pathlib import Path

import ezdxf


# ── helpers ──────────────────────────────────────────────────────────────────

def entity_centroid(e):
    t = e.dxftype()
    try:
        if t == 'LINE':
            return (e.dxf.start.x + e.dxf.end.x) / 2, (e.dxf.start.y + e.dxf.end.y) / 2
        elif t == 'LWPOLYLINE':
            pts = list(e.get_points())
            if pts:
                return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
        elif t in ('ARC', 'CIRCLE'):
            return e.dxf.center.x, e.dxf.center.y
        elif t == 'DIMENSION':
            dp2, dp3 = e.dxf.defpoint2, e.dxf.defpoint3
            return (dp2.x + dp3.x) / 2, (dp2.y + dp3.y) / 2
        elif t in ('TEXT', 'MTEXT', 'INSERT', 'POINT'):
            p = e.dxf.insert
            return p.x, p.y
        elif t == 'SPLINE':
            cpts = list(e.control_points)
            if cpts:
                return sum(p[0] for p in cpts) / len(cpts), sum(p[1] for p in cpts) / len(cpts)
    except Exception:
        pass
    return None


def poly_bbox(p):
    pts = [(pt[0], pt[1]) for pt in p.get_points()]
    xs = [pt[0] for pt in pts]; ys = [pt[1] for pt in pts]
    return min(xs), max(xs), min(ys), max(ys)


# ── 1. find outer apartment sheet boundaries ──────────────────────────────────

def find_outer_boundaries(doc, min_width=2000, min_height=3000):
    """Layer-0 large rectangles = one sheet per apartment."""
    bboxes = []
    for e in doc.modelspace():
        if e.dxftype() != 'LWPOLYLINE' or e.dxf.layer != '0':
            continue
        pts = list(e.get_points())
        if len(pts) < 3:
            continue
        x0, x1, y0, y1 = poly_bbox(e)
        w, h = x1 - x0, y1 - y0
        if w >= min_width and h >= min_height:
            bboxes.append((x0, x1, y0, y1))
    bboxes.sort(key=lambda b: b[0])
    return bboxes


# ── 2. find inner floor-plan boundary within one sheet ────────────────────────

def find_floorplan_boundary(all_entities, sheet_x0, sheet_x1, sheet_y0, sheet_y1,
                            drawing_layers=('BAR-GAL',), min_pts=4):
    """
    Return (x0, x1, y0, y1) of the largest closed polygon on a drawing layer
    whose centroid falls inside the sheet.  Falls back to the sheet bounds.
    """
    best_area = 0
    best_bbox = (sheet_x0, sheet_x1, sheet_y0, sheet_y1)

    for e in all_entities:
        if e.dxftype() != 'LWPOLYLINE':
            continue
        if e.dxf.layer not in drawing_layers:
            continue
        pts = list(e.get_points())
        if len(pts) < min_pts:
            continue
        x0, x1, y0, y1 = poly_bbox(e)
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if not (sheet_x0 <= cx <= sheet_x1 and sheet_y0 <= cy <= sheet_y1):
            continue
        area = (x1 - x0) * (y1 - y0)
        if area > best_area:
            best_area = area
            best_bbox = (x0, x1, y0, y1)

    return best_bbox


# ── 3. main split ─────────────────────────────────────────────────────────────

def split(input_path: str, output_dir: str,
          drawing_layers=('BAR-GAL',)):
    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading {input_path.name} ...")
    doc = ezdxf.readfile(str(input_path))
    all_entities = list(doc.modelspace())

    sheets = find_outer_boundaries(doc)
    if not sheets:
        print("ERROR: No apartment sheet boundaries found (layer-0 rectangles).")
        sys.exit(1)

    print(f"Found {len(sheets)} apartment sheets.")
    written = []

    for i, (sx0, sx1, sy0, sy1) in enumerate(sheets):
        # find inner floor-plan area
        fp_x0, fp_x1, fp_y0, fp_y1 = find_floorplan_boundary(
            all_entities, sx0, sx1, sy0, sy1, drawing_layers)
        fp_w = fp_x1 - fp_x0
        fp_h = fp_y1 - fp_y0
        print(f"  apt{i+1:02d}: sheet W={sx1-sx0:.0f}  floor-plan x=[{fp_x0:.0f},{fp_x1:.0f}] "
              f"y=[{fp_y0:.0f},{fp_y1:.0f}] W={fp_w:.0f} H={fp_h:.0f}")

        # build output doc
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
                    'color':    lyr.dxf.get('color', 7),
                    'linetype': lyr.dxf.get('linetype', 'Continuous'),
                })
            except Exception:
                pass

        out_msp = out_doc.modelspace()
        pad = 20   # small padding to capture boundary lines
        count = 0
        for e in all_entities:
            c = entity_centroid(e)
            if c is None:
                continue
            cx, cy = c
            if (fp_x0 - pad) <= cx <= (fp_x1 + pad) and \
               (fp_y0 - pad) <= cy <= (fp_y1 + pad):
                try:
                    out_msp.add_entity(e.copy())
                    count += 1
                except Exception:
                    pass

        stem = input_path.stem
        out_path = output_dir / f"{stem}_apt{i+1:02d}.dxf"
        out_doc.saveas(str(out_path))
        written.append((out_path, count))
        print(f"         -> {out_path.name}  ({count} entities)")

    print(f"\nDone — {len(written)} files in {output_dir}/")
    return written


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python split_drawings.py <input.dxf> <output_dir>")
        sys.exit(1)
    split(sys.argv[1], sys.argv[2])
