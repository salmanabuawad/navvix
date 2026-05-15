from __future__ import annotations

# CLAUDE-GUARD:
# Dimensions are annotations of semantic spans, not direct measurements of raw lines.
# Every dimension candidate must carry zone_id, semantic_type, and priority.

from itertools import count

from app.engine.types import BBox, DimensionCandidate, Orientation, SemanticSpan


def candidates_from_semantic_spans(spans: list[SemanticSpan], bbox: BBox) -> list[DimensionCandidate]:
    base = max(1.0, min(bbox.width, bbox.height))
    offset0 = max(42.0, base * 0.022)
    seq = count(1)
    candidates: list[DimensionCandidate] = []

    # Perimeter-first ordering. This keeps architectural priorities stable.
    ordered = sorted(spans, key=lambda s: (s.priority, s.orientation.value, s.axis, s.a))

    for i, span in enumerate(ordered):
        band = span.priority
        lane = i % 3
        offset = offset0 * (band + lane * 0.45)

        if span.orientation == Orientation.H:
            side = 1 if span.axis >= (bbox.y1 + bbox.y2) / 2 else -1
            p1 = (span.a, span.axis)
            p2 = (span.b, span.axis)
            base_point = ((span.a + span.b) / 2, span.axis + side * offset)
        else:
            side = 1 if span.axis >= (bbox.x1 + bbox.x2) / 2 else -1
            p1 = (span.axis, span.a)
            p2 = (span.axis, span.b)
            base_point = (span.axis + side * offset, (span.a + span.b) / 2)

        candidates.append(
            DimensionCandidate(
                id=f"D-{next(seq):05d}",
                orientation=span.orientation,
                p1=p1,
                p2=p2,
                base=base_point,
                value=span.length,
                zone_id=span.zone_id,
                semantic_type=span.semantic_type,
                priority=span.priority,
            )
        )

    return candidates
