"""
navvix_v12 — Architectural DXF dimensioning pipeline.

Steps:
  1. Isolation  — detect main floor-plan cluster; discard frame/table/title block
  2. Clean      — strip existing dimensions, text, annotations from isolated doc
  3. Axes       — extract real wall breakpoints (H and V) from clean geometry
  4. External   — chain + overall dims on all four sides
  5. Semantic   — classify grid cells: room / corridor / living / service / perimeter
  6. Internal   — W+H for rooms, width-only for corridors; skip everything else
"""

import argparse, json, math
from pathlib import Path

import ezdxf
import numpy as np
import matplotlib.pyplot as plt
from ezdxf import units

# ── Entity-type sets ─────────────────────────────────────────────────────────
GEOM  = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE"}
ANNOT = {"DIMENSION", "TEXT", "MTEXT", "INSERT", "LEADER", "MULTILEADER",
         "ACAD_TABLE", "ATTDEF", "ATTRIB"}


# ── Geometry helpers ─────────────────────────────────────────────────────────

def entity_pts(e):
    """Return a list of (x, y) for any entity that has a spatial footprint."""
    try:
        t = e.dxftype()
        if t == "LINE":
            return [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
        if t == "LWPOLYLINE":
            return [(p[0], p[1]) for p in e.get_points()]
        if t == "POLYLINE":
            return [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
        if t in ("TEXT", "MTEXT", "INSERT", "ATTRIB"):
            p = e.dxf.insert; return [(p.x, p.y)]
    except Exception:
        pass
    return []


def bb(ps):
    xs = [p[0] for p in ps]; ys = [p[1] for p in ps]
    return min(xs), min(ys), max(xs), max(ys)


def bb_hits(a, b):
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def copy_layers(src, dst):
    for lyr in src.layers:
        try:
            if lyr.dxf.name not in dst.layers:
                dst.layers.new(lyr.dxf.name, dxfattribs={"color": lyr.dxf.color})
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# STEP 1 — Isolation
# ═══════════════════════════════════════════════════════════════════════════

def isolate(input_dxf, output_dxf):
    """
    Cluster all geometry by spatial proximity, score each cluster, and
    extract only the highest-scoring one (the main floor plan).

    Scoring rewards:
      • high complexity (many lines / polylines)
      • central-left position
      • moderate area relative to drawing

    Scoring penalises:
      • right-side position  → title block / table
      • very large rectangle → outer frame / border
      • tall narrow shape    → schedule table
      • too few entities     → stray geometry
    """
    doc = ezdxf.readfile(str(input_dxf))
    msp = doc.modelspace()

    recs = []
    for e in msp:
        if e.dxftype() not in GEOM:
            continue
        ps = entity_pts(e)
        if not ps:
            continue
        b = bb(ps); w = b[2]-b[0]; h = b[3]-b[1]
        if max(w, h) < 2:
            continue
        recs.append({
            "e": e, "t": e.dxftype(), "b": b,
            "c": ((b[0]+b[2])/2, (b[1]+b[3])/2),
            "w": w, "h": h,
        })

    if not recs:
        raise ValueError("No geometry entities found in DXF")

    # ── v17 fix: trim spatial outliers before clustering ─────────────────────
    # Title blocks / tables / legends placed far from the floor plan inflate
    # the global bbox, which in turn inflates the clustering `eps` and causes
    # disparate features to merge. Drop entity centers that fall outside the
    # 2–98 percentile band by a generous margin, BEFORE bbox + clustering.
    outliers_dropped = 0
    if len(recs) >= 20:
        cs = np.array([r["c"] for r in recs], float)
        cx_lo, cx_hi = np.percentile(cs[:, 0], [2, 98])
        cy_lo, cy_hi = np.percentile(cs[:, 1], [2, 98])
        span_x = max(cx_hi - cx_lo, 1.0)
        span_y = max(cy_hi - cy_lo, 1.0)
        margin_x = span_x * 2.0
        margin_y = span_y * 2.0
        keep_mask = (
            (cs[:, 0] >= cx_lo - margin_x) & (cs[:, 0] <= cx_hi + margin_x) &
            (cs[:, 1] >= cy_lo - margin_y) & (cs[:, 1] <= cy_hi + margin_y)
        )
        outliers_dropped = int((~keep_mask).sum())
        if outliers_dropped > 0:
            recs = [r for i, r in enumerate(recs) if keep_mask[i]]

    all_pts = [(r["b"][0], r["b"][1]) for r in recs] + [(r["b"][2], r["b"][3]) for r in recs]
    gb = bb(all_pts); gw = gb[2]-gb[0]; gh = gb[3]-gb[1]

    # DBSCAN-style proximity clustering
    centers = np.array([r["c"] for r in recs], float)
    eps = max(150.0, min(gw, gh) * 0.025)
    visited = np.zeros(len(recs), bool)
    clusters = []
    for i in range(len(recs)):
        if visited[i]:
            continue
        stack = [i]; visited[i] = True; comp = []
        while stack:
            j = stack.pop(); comp.append(j)
            dists = np.sqrt(((centers - centers[j])**2).sum(axis=1))
            for k in np.where(dists <= eps)[0]:
                if not visited[k]:
                    visited[k] = True; stack.append(int(k))
        clusters.append(comp)

    cluster_infos = []
    for cid, comp in enumerate(clusters):
        xs = []; ys = []; tc = {}
        for idx in comp:
            r = recs[idx]; bx = r["b"]
            xs += [bx[0], bx[2]]; ys += [bx[1], bx[3]]
            tc[r["t"]] = tc.get(r["t"], 0) + 1
        cbbox = (min(xs), min(ys), max(xs), max(ys))
        bw = cbbox[2]-cbbox[0]; bh = cbbox[3]-cbbox[1]
        cx = (cbbox[0]+cbbox[2])/2; cy = (cbbox[1]+cbbox[3])/2
        nx = (cx - gb[0]) / max(1, gw)
        ny = (cy - gb[1]) / max(1, gh)
        aspect = max(bw, bh) / max(1, min(bw, bh))
        complexity = tc.get("LINE", 0) + 2.5 * (tc.get("LWPOLYLINE", 0) + tc.get("POLYLINE", 0))

        p_right  = 0.07 if nx > 0.72 else 1.0
        p_frame  = 0.04 if bw > gw*0.75 and bh > gh*0.70 else 1.0
        p_table  = 0.10 if aspect > 3.5 and (nx > 0.65 or ny < 0.12) else 1.0
        p_sparse = 0.04 if len(comp) < 10 else 1.0

        centrality = max(0.1, 1.6 - abs(nx - 0.38)*2.8 - abs(ny - 0.52)*1.4)
        area_ratio = (bw * bh) / max(1, gw * gh)
        area_sc    = max(0.1, min(1.5, area_ratio / 0.10))
        score = complexity * centrality * area_sc * p_right * p_frame * p_table * p_sparse

        cluster_infos.append({
            "id": cid, "bbox": cbbox, "score": score,
            "count": len(comp), "nx": nx, "ny": ny,
        })

    cluster_infos.sort(key=lambda x: x["score"], reverse=True)
    main = cluster_infos[0]
    mb = main["bbox"]; mw = mb[2]-mb[0]; mh = mb[3]-mb[1]
    pad = max(100, min(mw, mh) * 0.07)
    eb = (mb[0]-pad, mb[1]-pad, mb[2]+pad, mb[3]+pad)

    nd = ezdxf.new(dxfversion=doc.dxfversion); nd.units = doc.units
    copy_layers(doc, nd); nm = nd.modelspace()
    copied = 0
    rejected_outside = 0
    for e in msp:
        ps = entity_pts(e)
        if not ps:
            continue
        etest = bb(ps)
        if not bb_hits(etest, eb):
            continue
        # v17 fix: require the entity's *center* to fall inside eb, not just
        # any corner. A legend at +5000 with one stray line into the plan
        # otherwise leaks in via bb_hits's overlap check.
        ecx = (etest[0] + etest[2]) / 2
        ecy = (etest[1] + etest[3]) / 2
        if not (eb[0] <= ecx <= eb[2] and eb[1] <= ecy <= eb[3]):
            rejected_outside += 1
            continue
        ew = etest[2]-etest[0]; eh = etest[3]-etest[1]
        if ew > mw * 0.82 and eh > mh * 0.62:   # large outer frame → skip
            continue
        try:
            nm.add_entity(e.copy()); copied += 1
        except Exception:
            pass

    nd.saveas(str(output_dxf))
    return {
        "global_bbox":     gb,
        "main_bbox":       mb,
        "expanded_bbox":   eb,
        "copied":          copied,
        "clusters":        cluster_infos[:10],
        "v17_fix": {
            "outliers_dropped":    outliers_dropped,
            "rejected_outside_eb": rejected_outside,
            "rule":                "isolate main plan first; exclude legend/table/title/frame before wall extraction",
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# STEP 2 — Clean geometry
# ═══════════════════════════════════════════════════════════════════════════

def clean_geometry(doc):
    """Remove all annotation / dimension entities from the modelspace in-place."""
    msp = doc.modelspace()
    to_del = [e for e in msp if e.dxftype() in ANNOT]
    for e in to_del:
        try:
            msp.delete_entity(e)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# STEP 3 — Wall axis extraction
# ═══════════════════════════════════════════════════════════════════════════

def _seg_add(segs, x1, y1, x2, y2, min_len=50):
    dx = x2-x1; dy = y2-y1; L = math.hypot(dx, dy)
    if L < min_len:
        return
    if   abs(dx) >= abs(dy) * 8: segs.append((x1, y1, x2, y2, "h", L))
    elif abs(dy) >= abs(dx) * 8: segs.append((x1, y1, x2, y2, "v", L))


def extract_segments(doc):
    segs = []
    for e in doc.modelspace():
        try:
            t = e.dxftype()
            if t == "LINE":
                a, b = e.dxf.start, e.dxf.end
                _seg_add(segs, a.x, a.y, b.x, b.y)
            elif t == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points()]
                if e.closed and pts: pts.append(pts[0])
                for a, b in zip(pts, pts[1:]): _seg_add(segs, a[0], a[1], b[0], b[1])
            elif t == "POLYLINE":
                pts = [(v.dxf.location.x, v.dxf.location.y) for v in e.vertices]
                if e.is_closed and pts: pts.append(pts[0])
                for a, b in zip(pts, pts[1:]): _seg_add(segs, a[0], a[1], b[0], b[1])
        except Exception:
            pass
    return segs


def cluster_vals(vals, tol):
    vals = sorted(vals)
    if not vals:
        return []
    groups = [[vals[0]]]
    for v in vals[1:]:
        if abs(v - groups[-1][-1]) <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups]


def prune_axes(vals, lo, hi, max_n=12):
    """
    Enforce a minimum inter-axis gap, then thin to max_n by iteratively
    removing the interior point whose removal creates the smallest merged gap
    (least information loss).
    """
    vals = sorted(set([lo] + [v for v in vals if lo < v < hi] + [hi]))
    min_gap = (hi - lo) * 0.045
    out = [vals[0]]
    for v in vals[1:]:
        if v - out[-1] >= min_gap:
            out.append(v)
    while len(out) > max_n:
        if len(out) <= 2:
            break
        costs = [out[i+1] - out[i-1] for i in range(1, len(out)-1)]
        rm = 1 + costs.index(min(costs))
        out.pop(rm)
    return out


# ═══════════════════════════════════════════════════════════════════════════
# STEP 5 — Semantic cell classification
# ═══════════════════════════════════════════════════════════════════════════

def classify_cell(nx, ny, cw, ch, w, h):
    """
    Returns one of: room | corridor | living | service | perimeter

    Only 'room' and 'corridor' receive internal dimensions.
    Everything else is skipped.
    """
    ar  = (cw * ch) / max(1, w * h)
    asp = max(cw, ch) / max(1, min(cw, ch))

    if nx < 0.07 or nx > 0.93 or ny < 0.07 or ny > 0.93:
        return "perimeter"   # balcony / terrace / outer strip
    if ar > 0.18:
        return "living"      # large open space → no dims
    if ar < 0.014:
        return "service"     # WC / storage / very small → no dims
    if asp >= 3.2:
        return "corridor"    # long narrow → width only
    return "room"            # medium area → W + H


# ═══════════════════════════════════════════════════════════════════════════
# STEP 4 + 6 — Dim helper + generate
# ═══════════════════════════════════════════════════════════════════════════

def add_dim(msp, p1, p2, base, angle, layer, style, min_len, created, kind):
    L = math.hypot(p2[0]-p1[0], p2[1]-p1[1])
    if L < min_len:
        return
    try:
        d = msp.add_linear_dim(base=base, p1=p1, p2=p2, angle=angle,
                               dimstyle=style, dxfattribs={"layer": layer})
        d.render()
        created.append({"kind": kind, "length": round(L, 1),
                        "p1": list(p1), "p2": list(p2)})
    except Exception:
        pass


def generate(isolated_dxf, output_dxf):
    doc = ezdxf.readfile(str(isolated_dxf))

    # Step 2: strip annotations
    clean_geometry(doc)

    # Step 3: wall segments from clean geometry only
    segs = extract_segments(doc)
    if not segs:
        raise ValueError("No wall segments found after cleaning — check isolation step")

    all_xs = [c for s in segs for c in (s[0], s[2])]
    all_ys = [c for s in segs for c in (s[1], s[3])]

    # Robust bounds: 2nd–98th percentile rejects stray outliers
    xlo, xhi = np.percentile(all_xs, [2, 98])
    ylo, yhi = np.percentile(all_ys, [2, 98])

    segs_in = [
        s for s in segs
        if xlo-50 <= s[0] <= xhi+50 and xlo-50 <= s[2] <= xhi+50
        and ylo-50 <= s[1] <= yhi+50 and ylo-50 <= s[3] <= yhi+50
    ]
    if not segs_in:
        segs_in = segs

    xs2 = [c for s in segs_in for c in (s[0], s[2])]
    ys2 = [c for s in segs_in for c in (s[1], s[3])]
    minx, miny, maxx, maxy = min(xs2), min(ys2), max(xs2), max(ys2)
    w = maxx-minx; h = maxy-miny
    base_dim = min(w, h)
    tol = max(20, base_dim * 0.005)

    # Breakpoints from actual segment endpoints (not all-values pool)
    x_breaks = []; y_breaks = []
    for x1, y1, x2, y2, ori, L in segs_in:
        if ori == "h":
            x_breaks += [x1, x2]        # H-seg endpoints → X breakpoints
            y_breaks.append((y1+y2)/2)  # H-seg Y position  → Y breakpoint
        else:
            y_breaks += [y1, y2]        # V-seg endpoints → Y breakpoints
            x_breaks.append((x1+x2)/2)  # V-seg X position  → X breakpoint

    xa = prune_axes(cluster_vals(x_breaks, tol), minx, maxx, 12)
    ya = prune_axes(cluster_vals(y_breaks, tol), miny, maxy, 12)

    # Dim style
    doc.units = units.MM
    for lyr_name in ["NAVVIX_V12_EXTERNAL", "NAVVIX_V12_INTERNAL"]:
        if lyr_name not in doc.layers:
            doc.layers.new(lyr_name, dxfattribs={"color": 7})

    blk_name = "NAVVIX_CLOSED_FILLED_ARROW"
    if blk_name not in doc.blocks:
        blk = doc.blocks.new(blk_name)
        blk.add_solid([(0, 0), (1, .28), (1, -.28), (0, 0)])

    sty = "NAVVIX_V12_DIM"
    ds  = doc.dimstyles.get(sty) if sty in doc.dimstyles else doc.dimstyles.new(sty)
    ds.dxf.dimblk = ds.dxf.dimblk1 = ds.dxf.dimblk2 = blk_name
    ds.dxf.dimasz = max(20,  base_dim * 0.008)
    ds.dxf.dimtxt = max(30,  base_dim * 0.010)
    ds.dxf.dimexo = max(8,   base_dim * 0.002)
    ds.dxf.dimexe = max(20,  base_dim * 0.006)
    ds.dxf.dimgap = max(8,   base_dim * 0.002)
    ds.dxf.dimtad = 1; ds.dxf.dimjust = 0

    msp     = doc.modelspace()
    created = []
    chain   = max(250, base_dim * 0.080)
    overall = max(500, base_dim * 0.155)
    min_len = max(250, base_dim * 0.040)

    # ── STEP 4: External dims ─────────────────────────────────────────────
    for a, b in zip(xa, xa[1:]):
        add_dim(msp,(a,maxy),(b,maxy), ((a+b)/2, maxy+chain),  0, "NAVVIX_V12_EXTERNAL",sty,min_len,created,"top_chain")
        add_dim(msp,(a,miny),(b,miny), ((a+b)/2, miny-chain),  0, "NAVVIX_V12_EXTERNAL",sty,min_len,created,"bot_chain")
    add_dim(msp,(minx,maxy),(maxx,maxy), ((minx+maxx)/2, maxy+overall), 0, "NAVVIX_V12_EXTERNAL",sty,min_len,created,"top_overall")
    add_dim(msp,(minx,miny),(maxx,miny), ((minx+maxx)/2, miny-overall), 0, "NAVVIX_V12_EXTERNAL",sty,min_len,created,"bot_overall")

    for a, b in zip(ya, ya[1:]):
        add_dim(msp,(minx,a),(minx,b), (minx-chain, (a+b)/2), 90, "NAVVIX_V12_EXTERNAL",sty,min_len,created,"left_chain")
        add_dim(msp,(maxx,a),(maxx,b), (maxx+chain, (a+b)/2), 90, "NAVVIX_V12_EXTERNAL",sty,min_len,created,"right_chain")
    add_dim(msp,(minx,miny),(minx,maxy), (minx-overall,(miny+maxy)/2), 90, "NAVVIX_V12_EXTERNAL",sty,min_len,created,"left_overall")
    add_dim(msp,(maxx,miny),(maxx,maxy), (maxx+overall,(miny+maxy)/2), 90, "NAVVIX_V12_EXTERNAL",sty,min_len,created,"right_overall")

    # ── STEP 5 + 6: Semantic cells → internal dims ────────────────────────
    cells = []
    for a, b in zip(xa, xa[1:]):
        for c, d in zip(ya, ya[1:]):
            cw = b-a; ch = d-c
            nx = ((a+b)/2 - minx) / max(1, w)
            ny = ((c+d)/2 - miny) / max(1, h)
            kind = classify_cell(nx, ny, cw, ch, w, h)
            cells.append({"bbox": [a, c, b, d], "kind": kind,
                          "cw": round(cw, 1), "ch": round(ch, 1)})

    int_count = 0
    for cell in cells:
        if int_count >= 14:
            break
        kind = cell["kind"]
        if kind not in ("room", "corridor"):
            continue   # living / service / perimeter → skip

        a, c, b, d = cell["bbox"]
        cw = b-a; ch = d-c
        off = max(80, min(cw, ch) * 0.15)
        ml  = min_len * 0.65

        if kind == "room":
            add_dim(msp,(a+50,c+off),(b-50,c+off), ((a+b)/2, c+off), 0,  "NAVVIX_V12_INTERNAL",sty,ml,created,"room_W")
            add_dim(msp,(b-off,c+50),(b-off,d-50), (b-off,(c+d)/2),  90, "NAVVIX_V12_INTERNAL",sty,ml,created,"room_H")
            int_count += 2
        else:  # corridor — width only
            if cw <= ch:
                add_dim(msp,(a+50,c+off),(b-50,c+off), ((a+b)/2, c+off), 0,  "NAVVIX_V12_INTERNAL",sty,ml,created,"cor_W")
            else:
                add_dim(msp,(b-off,c+50),(b-off,d-50), (b-off,(c+d)/2),  90, "NAVVIX_V12_INTERNAL",sty,ml,created,"cor_W")
            int_count += 1

    doc.saveas(str(output_dxf))
    return {
        "bbox":           [minx, miny, maxx, maxy],
        "x_axes":         xa,
        "y_axes":         ya,
        "segments":       len(segs_in),
        "dimensions":     created,
        "internal_count": int_count,
        "cells":          cells,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Preview (PNG + PDF)
# ═══════════════════════════════════════════════════════════════════════════

def preview(dxf_path, png_path, pdf_path, crop):
    doc = ezdxf.readfile(str(dxf_path))
    msp = doc.modelspace()
    minx, miny, maxx, maxy = crop
    pad = max(maxx-minx, maxy-miny) * 0.10

    fig, ax = plt.subplots(figsize=(14, 10), facecolor="white")

    for e in msp:
        try:
            t = e.dxftype()
            if t == "LINE":
                a, b = e.dxf.start, e.dxf.end
                if minx-pad <= a.x <= maxx+pad and miny-pad <= a.y <= maxy+pad:
                    ax.plot([a.x, b.x], [a.y, b.y], color="black", lw=0.6)
            elif t == "LWPOLYLINE":
                pts = [(p[0], p[1]) for p in e.get_points()]
                if e.closed and pts: pts.append(pts[0])
                pts = [q for q in pts if minx-pad <= q[0] <= maxx+pad and miny-pad <= q[1] <= maxy+pad]
                if len(pts) > 1:
                    ax.plot([q[0] for q in pts], [q[1] for q in pts], color="black", lw=0.6)
            elif t == "DIMENSION":
                p1, p2, base = e.dxf.defpoint2, e.dxf.defpoint3, e.dxf.defpoint
                ang = float(getattr(e.dxf, "angle", 0) or 0)
                if abs(ang) < 45:
                    ax.plot([p1.x, p2.x], [base.y, base.y], color="#2060C0", lw=0.45)
                    ax.text((p1.x+p2.x)/2, base.y, str(round(abs(p2.x-p1.x))),
                            ha="center", va="bottom", fontsize=4.5, color="#2060C0")
                else:
                    ax.plot([base.x, base.x], [p1.y, p2.y], color="#2060C0", lw=0.45)
                    ax.text(base.x, (p1.y+p2.y)/2, str(round(abs(p2.y-p1.y))),
                            ha="left", va="center", fontsize=4.5, color="#2060C0")
        except Exception:
            pass

    ax.set_xlim(minx-pad, maxx+pad); ax.set_ylim(miny-pad, maxy+pad)
    ax.set_aspect("equal"); ax.axis("off")
    fig.tight_layout(pad=0)
    fig.savefig(str(png_path), dpi=220, bbox_inches="tight", facecolor="white")
    fig.savefig(str(pdf_path), dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(description="navvix_v12 — architectural DXF dimensioner")
    ap.add_argument("--input",      required=True, help="Input DXF path")
    ap.add_argument("--output-dir", required=True, help="Output directory")
    args = ap.parse_args()

    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    iso = out / "isolated_main.dxf"
    dim = out / "dimensioned.dxf"

    print("[1/4] Isolating main floor plan…")
    iso_report = isolate(args.input, iso)
    print(f"      copied {iso_report['copied']} entities  "
          f"(top cluster score={iso_report['clusters'][0]['score']:.1f})")

    print("[2/4] Generating dimensions…")
    gen = generate(iso, dim)
    print(f"      x_axes={len(gen['x_axes'])}  y_axes={len(gen['y_axes'])}  "
          f"dims={len(gen['dimensions'])}  internal={gen['internal_count']}")

    print("[3/4] Rendering preview…")
    preview(dim, out / "preview.png", out / "preview.pdf", tuple(gen["bbox"]))

    print("[4/4] Writing report…")
    report = {
        "input":           args.input,
        "isolated_dxf":    str(iso),
        "dimensioned_dxf": str(dim),
        "preview_png":     str(out / "preview.png"),
        "preview_pdf":     str(out / "preview.pdf"),
        "isolation":       iso_report,
        "generation":      gen,
    }
    (out / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    print(json.dumps({
        "status":          "ok",
        "dimensioned_dxf": str(dim),
        "preview_pdf":     str(out / "preview.pdf"),
        "x_axes":          len(gen["x_axes"]),
        "y_axes":          len(gen["y_axes"]),
        "dimensions":      len(gen["dimensions"]),
        "internal":        gen["internal_count"],
    }, indent=2))


if __name__ == "__main__":
    main()
