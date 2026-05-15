from __future__ import annotations

# CLAUDE-GUARD:
# This module only turns semantic DimensionCandidate objects into CAD DIMENSION entities.
# Do not call it with raw line segments.

import ezdxf

from app.engine.types import DimensionCandidate, Orientation


def add_dimensions(doc: ezdxf.EzDxfDocument, candidates: list[DimensionCandidate], style_name: str = "ISO-25") -> list[dict]:
    msp = doc.modelspace()
    layer = "NAVVIX_SEMANTIC_DIMS"
    if layer not in doc.layers:
        doc.layers.new(layer, dxfattribs={"color": 7})

    created: list[dict] = []
    for candidate in candidates:
        try:
            angle = 0 if candidate.orientation == Orientation.H else 90
            dim = msp.add_linear_dim(
                base=candidate.base,
                p1=candidate.p1,
                p2=candidate.p2,
                angle=angle,
                dimstyle=style_name,
                dxfattribs={"layer": layer},
            )
            dim.render()
            created.append(
                {
                    "id": candidate.id,
                    "zone_id": candidate.zone_id,
                    "semantic_type": candidate.semantic_type,
                    "priority": candidate.priority,
                    "value": round(candidate.value, 3),
                    "orientation": candidate.orientation.value,
                }
            )
        except Exception as exc:
            created.append({"id": candidate.id, "error": str(exc)})
    return created
