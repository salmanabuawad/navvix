from __future__ import annotations

# CLAUDE-GUARD:
# Validation protects against regression to page-frame/table dimensions and raw geometry clutter.

from app.engine.types import DimensionCandidate


def validate_dimensions(candidates: list[DimensionCandidate]) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    if not candidates:
        errors.append("No dimensions generated")

    missing_ownership = [c.id for c in candidates if not c.zone_id or not c.semantic_type]
    if missing_ownership:
        errors.append(f"Dimensions missing semantic ownership: {missing_ownership[:10]}")

    exterior_count = sum(1 for c in candidates if c.zone_id == "exterior_shell")
    if exterior_count == 0:
        warnings.append("No exterior_shell dimensions generated; perimeter-first strategy may be weak")

    internal_count = len(candidates) - exterior_count
    if internal_count > exterior_count * 4 and len(candidates) > 30:
        warnings.append("Interior dimensions dominate output; check semantic filtering")

    return {"valid": not errors, "errors": errors, "warnings": warnings}
