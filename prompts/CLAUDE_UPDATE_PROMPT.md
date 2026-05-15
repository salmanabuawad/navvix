# Claude Update Prompt

You are working on the Navvix production codebase.

Before editing, read:

- `docs/CLAUDE_DO_NOT_OVERWRITE.md`
- `docs/ENGINE_RULES.md`
- `backend/app/engine/README.md`

Your task is to improve the current production engine, not to create a new version folder.

Critical constraints:

1. Keep React/TypeScript frontend.
2. Keep Python/FastAPI backend.
3. Keep PostgreSQL persistence.
4. Preserve modular engine structure.
5. Do not dimension raw lines directly.
6. Do not dimension table/legend/title/frame geometry.
7. Every dimension must have semantic ownership.
8. Maintain perimeter-first strategy.
9. Interior dimensions must be selective.
10. Do not overwrite guard comments.

When changing the engine, update or add tests under `tests/` and update `docs/ENGINE_RULES.md` if behavior changes.
