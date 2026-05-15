# Navvix production DXF engine

CLAUDE-GUARD: Do not replace this engine with raw line dimensioning.

This engine is intentionally modular:

- `isolation/` detects and crops the main architectural floorplan.
- `geometry/` builds a normalized line registry.
- `semantic/` merges local/topological wall spans and assigns ownership.
- `dimensions/` creates dimension candidates from semantic spans only.
- `styling/` applies CAD dimension style.
- `rendering/` creates preview PDF/PNG.
- `validation/` rejects obvious bad outputs.

Every dimension must come from a semantic span, never directly from a raw CAD entity.
