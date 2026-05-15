# Claude Guard Instructions

These rules are part of the codebase contract.

Do not overwrite the engine with a raw line-dimensioning implementation.

## Non-negotiable rules

1. Do not dimension page frames, title blocks, legends, tables, schedules, or note boxes.
2. Do not create dimensions directly from raw CAD entities.
3. Every generated dimension must have semantic ownership.
4. Dimension ownership must be one of:
   - exterior_shell
   - room_zone
   - corridor_zone
   - stair_zone
   - balcony_zone
   - service_core
   - critical_offset
5. Use perimeter-first dimension strategy.
6. Interior dimensions must be selective and minimal.
7. Do not add global same-length grouping.
8. Grouping must be local/topological.
9. Rendering must crop to isolated architectural content, not the full sheet.
10. Preserve the modular engine structure.

## Allowed changes

Claude may improve logic inside existing modules, add tests, and add new helpers.

## Forbidden changes

Claude must not:

- replace the engine with a single monolithic script
- reintroduce version folders such as `v10`, `v17`, `v18`
- commit `.claude`, `.git`, `node_modules`, or `dist`
- remove the guard comments in engine modules
