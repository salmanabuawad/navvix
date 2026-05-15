from __future__ import annotations

# CLAUDE-GUARD:
# Preserve ISO-25-style compact dimensions unless explicitly improving based on sample style extraction.

import ezdxf
from ezdxf import units


def setup_dimstyle(doc: ezdxf.EzDxfDocument, style_name: str = "ISO-25") -> str:
    doc.units = units.MM
    if style_name not in doc.dimstyles:
        doc.dimstyles.new(style_name)
    ds = doc.dimstyles.get(style_name)

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

    # Let CAD viewers move text/arrows when space is tight.
    for key, value in [("dimtix", 0), ("dimtofl", 1), ("dimtmove", 1), ("dimatfit", 3)]:
        try:
            setattr(ds.dxf, key, value)
        except Exception:
            pass
    return style_name
