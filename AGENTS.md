# Agent guidance

## Architecture

- Keep the browser-facing web image separate from the engine-derived API image.
- Never mount the Docker socket into either service.
- Commands must come from the trusted recipe registry and run with
  `create_subprocess_exec`; never execute project-supplied shell strings.
- Keep all project paths beneath `STUDIO_WORKSPACE_ROOT`.
- Preserve the exact command, engine version, timestamps, exit code, logs, and
  artifact metadata in the production record.

## Validation

Before committing:

```bash
cd apps/web && pnpm lint && pnpm build
cd services/api && uv run --extra dev pytest
docker compose config
```

Container changes must remain valid for Linux AMD64 and ARM64.
