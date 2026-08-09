# Skills Video Studio

A staged web control plane for
[`skills-video-engine`](https://github.com/mhuot/skills-video-engine). Studio
turns video production into visible, independently rerunnable phases with
live logs, artifacts, cancellation, and production evidence.

## Production phases

1. Prepare
2. Narrate
3. Measure
4. Compose
5. Validate
6. Snapshot
7. Render
8. QA

Each job records the trusted command, engine version, timestamps, result, and
events. Project files remain under `/workspace`; operational state remains
under `/data`.

Studio currently exposes the public `explainer-video` recipe. The recipe
catalog is discovered from the API, so promo-video and future public skills
can be added as reviewed adapters without changing the project dialog.

Creating a project establishes its manifest and production workspace. The
authoring workflow then supplies the project-specific script, narration tools,
and HyperFrames composition before those phases are run.

## Run the sidecar deployment

```bash
docker compose up --build
```

Open <http://localhost:8080>.

This starts:

- `studio-web`: browser UI and reverse proxy
- `engine-api`: FastAPI service derived from
  `ghcr.io/mhuot/skills-video-engine:0.3.0`

The containers communicate over an internal network. The web service has
neither the project volume nor the Docker socket.

Projects contain executable HTML, JavaScript, and optional authoring scripts.
This release is intended for trusted local projects. Multi-user or untrusted
uploads require the planned isolated-worker deployment.

## Run the all-in-one deployment

```bash
docker compose -f compose.all-in-one.yaml up --build
```

The all-in-one image serves the same compiled React application from the
engine API process.

## Develop

Start the API:

```bash
cd services/api
uv run --extra dev uvicorn studio_api.main:app --reload
```

Start the UI:

```bash
cd apps/web
pnpm install
pnpm dev
```

Vite proxies `/api` and `/health` to `localhost:8000`.

## API

The OpenAPI document is available at `/openapi.json`. Important endpoints:

- `POST /api/v1/projects`
- `GET /api/v1/projects/{id}`
- `POST /api/v1/projects/{id}/jobs`
- `GET /api/v1/jobs/{id}`
- `POST /api/v1/jobs/{id}/cancel`
- `GET /api/v1/jobs/{id}/events`
- `GET /api/v1/jobs/{id}/log`
- `GET /api/v1/projects/{id}/artifacts`

See [docs/architecture.md](docs/architecture.md) for boundaries and lifecycle.

## MCP server

`services/mcp` ships `studio-mcp`, a dependency-free MCP stdio server that
exposes the studio API to agent clients as typed tools: projects, phases as
asynchronous jobs, logs, and artifact listings. All execution stays inside
the studio's trusted recipe commands.

With a studio deployment running, register it in Claude Code:

```bash
claude mcp add studio-video --env STUDIO_API_URL=http://localhost:8080 \
  -- uv run --project /path/to/skills-video-studio/services/mcp studio-mcp
```

Agents start work with `run_phase`, which returns a job id immediately, then
poll `get_job` and read `get_job_log` — no shell access or Docker socket is
required by the client.

## License

MIT
