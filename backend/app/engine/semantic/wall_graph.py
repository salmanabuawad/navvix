from __future__ import annotations

# CLAUDE-GUARD:
# Grouping must be local and topological. Do not group globally by same length alone.

from collections import defaultdict
from itertools import count

from app.engine.types import BBox, LineSegment, Orientation, SemanticSpan


def _snap(v: float, step: float) -> float:
    return round(v / step) * step


def _is_perimeter(axis: float, orientation: Orientation, bbox: BBox, band: float) -> bool:
    if orientation == Orientation.H:
        return min(abs(axis - bbox.y1), abs(axis - bbox.y2)) <= band
    return min(abs(axis - bbox.x1), abs(axis - bbox.x2)) <= band


def semantic_spans_from_lines(lines: list[LineSegment], bbox: BBox) -> tuple[list[SemanticSpan], list[dict]]:
    """Create semantic spans from a line registry.

    The algorithm intentionally performs local/topological grouping only:
    - same orientation
    - close snapped axis
    - touching/overlapping spans
    - transition filtering

    It does not globally merge same-length lines.
    """
    if not lines:
        return [], []

    base = max(1.0, min(bbox.width, bbox.height))
    snap = max(8.0, base * 0.003)
    gap_tol = max(18.0, base * 0.006)
    short_threshold = max(115.0, base * 0.030)
    perimeter_band = max(90.0, base * 0.030)

    dedup: dict[tuple, dict] = {}
    source_ids: dict[tuple, list[str]] = defaultdict(list)

    for line in lines:
        if line.length < max(45.0, base * 0.010):
            continue
        key = (line.orientation.value, _snap(line.axis, snap), _snap(line.a, snap), _snap(line.b, snap))
        source_ids[key].append(line.id)
        if key not in dedup or line.length > dedup[key]["length"]:
            dedup[key] = {
                "orientation": line.orientation,
                "axis": _snap(line.axis, snap),
                "a": _snap(line.a, snap),
                "b": _snap(line.b, snap),
                "length": abs(_snap(line.b, snap) - _snap(line.a, snap)),
            }

    groups: dict[tuple[str, float], list[dict]] = defaultdict(list)
    for raw in dedup.values():
        groups[(raw["orientation"].value, raw["axis"])].append(raw)

    semantic: list[SemanticSpan] = []
    filtered: list[dict] = []
    seq = count(1)

    for (_ori, _axis), group in groups.items():
        group = sorted(group, key=lambda item: item["a"])
        current: dict | None = None

        for item in group:
            is_perimeter = _is_perimeter(item["axis"], item["orientation"], bbox, perimeter_band)
            if item["length"] < short_threshold and not is_perimeter:
                filtered.append({**item, "reason": "short_non_perimeter_transition"})
                continue

            if current is None:
                current = item.copy()
                continue

            gap = item["a"] - current["b"]
            if gap <= gap_tol:
                current["b"] = max(current["b"], item["b"])
                current["length"] = current["b"] - current["a"]
            else:
                semantic.append(_to_span(current, bbox, next(seq)))
                current = item.copy()

        if current is not None:
            semantic.append(_to_span(current, bbox, next(seq)))

    semantic = [s for s in semantic if s.length >= max(60.0, base * 0.014)]
    semantic.sort(key=lambda s: (s.priority, s.orientation.value, s.axis, s.a))
    return semantic, filtered


def _to_span(item: dict, bbox: BBox, idx: int) -> SemanticSpan:
    orientation: Orientation = item["orientation"]
    axis = item["axis"]
    a = item["a"]
    b = item["b"]
    length = b - a

    # Basic zone ownership. Future Claude work should improve room/corridor detection,
    # but it must preserve this ownership concept.
    if orientation == Orientation.H:
        dist_per = min(abs(axis - bbox.y1), abs(axis - bbox.y2))
    else:
        dist_per = min(abs(axis - bbox.x1), abs(axis - bbox.x2))

    perimeter_band = max(90.0, min(bbox.width, bbox.height) * 0.030)
    if dist_per <= perimeter_band:
        zone_id = "exterior_shell"
        semantic_type = "exterior_span"
        priority = 1
    else:
        zone_id = "local_zone"
        semantic_type = "local_wall_span"
        priority = 3

    return SemanticSpan(
        id=f"S-{idx:05d}",
        orientation=orientation,
        a=a,
        b=b,
        axis=axis,
        length=length,
        zone_id=zone_id,
        semantic_type=semantic_type,
        priority=priority,
    )
