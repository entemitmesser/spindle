# Spindle

Self-hosted automatic disc ripper. See `CLAUDE.md` and `docs/architecture.md` for the
full contract, and `docs/PLAN.md` for the phased build order.

## Development

```bash
uv sync
uv run uvicorn spindle.main:create_app --factory --reload --port 8478

cd web
pnpm install
pnpm dev
```
