# Navvix Engine Rules

## Architectural drafting target

The output must resemble a human-created architectural dimension plan, not geometry debug output.

## Pipeline

```text
DXF
→ main-plan isolation
→ normalized line registry
→ local wall graph
→ semantic span grouping
→ dimension ownership
→ perimeter-first strategy
→ styling/rendering
→ validation
```

## Isolation

Reject:

- full-page frames
- title blocks
- legends
- schedules
- tables
- annotation boxes
- sparse border-only rectangles

## Line grouping

Similarity alone is not enough.

Allowed grouping signals:

- same orientation
- shared/touching endpoints
- overlapping spans
- close parallel wall-pair distance
- same local connected component

Forbidden grouping signals by themselves:

- same length globally
- same X/Y globally
- symmetry globally
- center bias

## Dimension ownership

Every dimension must have:

```json
{
  "zone_id": "...",
  "semantic_type": "...",
  "priority": 1
}
```

## Dimension priority

1. exterior shell
2. major room spans
3. corridor widths
4. stair block
5. balconies
6. local internal details

## Validation

Reject output if:

- dimensions are attached to table/frame/title geometry
- long dimensions cross unrelated zones
- center is unreadable
- there is no exterior/perimeter strategy
- too many tiny connector dimensions are generated
