from __future__ import annotations

import asyncio
import json
import platform
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings
from .models import (
    Artifact,
    Health,
    Job,
    JobCreate,
    JobStatus,
    Project,
    ProjectCreate,
    ProjectDetail,
)
from .recipes import RECIPES, get_operation
from .runner import JobRunner
from .store import Store


def project_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug[:48] or "video-project"


def confined_project_path(project: Project, settings: Settings) -> Path:
    project_path = Path(project.path).resolve()
    if not project_path.is_relative_to(settings.workspace_root):
        raise HTTPException(status_code=409, detail="Project path escapes the configured workspace")
    return project_path


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or Settings.from_env()
    app_settings.initialize()
    store = Store(app_settings.data_root / "studio.sqlite3")
    runner = JobRunner(store, app_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if app_settings.seed_demo and not store.list_projects():
            create_project_record(
                store,
                app_settings,
                ProjectCreate(name="Skills Video Engine", recipe="explainer-video"),
            )
        yield

    app = FastAPI(
        title="Skills Video Studio API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.state.store = store
    app.state.runner = runner

    @app.get("/health", response_model=Health)
    def health() -> Health:
        return Health(
            status="ok",
            engine_version=app_settings.engine_version,
            platform=platform.machine(),
        )

    @app.get("/api/v1/recipes")
    def list_recipes() -> list[dict[str, object]]:
        return [
            {
                "id": name,
                "label": name.replace("-", " ").title(),
                "phases": [phase.value for phase in operations],
            }
            for name, operations in RECIPES.items()
        ]

    @app.post("/api/v1/projects", response_model=Project, status_code=201)
    def create_project(payload: ProjectCreate) -> Project:
        if payload.recipe not in RECIPES:
            raise HTTPException(status_code=422, detail="Unsupported recipe")
        return create_project_record(store, app_settings, payload)

    @app.get("/api/v1/projects", response_model=list[Project])
    def list_projects() -> list[Project]:
        return store.list_projects()

    @app.get("/api/v1/projects/{project_id}", response_model=ProjectDetail)
    def get_project(project_id: str) -> ProjectDetail:
        project = store.get_project_detail(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        return project

    @app.post("/api/v1/projects/{project_id}/jobs", response_model=Job, status_code=202)
    async def create_job(project_id: str, payload: JobCreate) -> Job:
        project = store.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        operation = get_operation(project.recipe, payload.phase)
        job = store.create_job(
            project.id,
            payload.phase,
            operation.command,
            app_settings.engine_version,
        )
        runner.enqueue(job, project)
        return job

    @app.get("/api/v1/jobs/{job_id}", response_model=Job)
    def get_job(job_id: str) -> Job:
        job = store.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return job

    @app.post("/api/v1/jobs/{job_id}/cancel", response_model=Job)
    async def cancel_job(job_id: str) -> Job:
        try:
            return await runner.cancel(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Job not found") from exc

    @app.get("/api/v1/jobs/{job_id}/events")
    async def job_events(request: Request, job_id: str) -> StreamingResponse:
        if not store.get_job(job_id):
            raise HTTPException(status_code=404, detail="Job not found")

        async def stream() -> AsyncIterator[str]:
            last_id = int(request.headers.get("last-event-id", "0"))
            while not await request.is_disconnected():
                events = store.list_events(job_id, last_id)
                for event in events:
                    last_id = event.id
                    yield (
                        f"id: {event.id}\n"
                        f"event: {event.kind}\n"
                        f"data: {event.model_dump_json()}\n\n"
                    )
                job = store.get_job(job_id)
                if job and job.status in {
                    JobStatus.SUCCEEDED,
                    JobStatus.FAILED,
                    JobStatus.CANCELED,
                }:
                    yield f"event: complete\ndata: {job.model_dump_json()}\n\n"
                    return
                yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    @app.get("/api/v1/projects/{project_id}/artifacts", response_model=list[Artifact])
    def list_artifacts(project_id: str) -> list[Artifact]:
        project = store.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        root = confined_project_path(project, app_settings)
        artifacts: list[Artifact] = []
        production = (root / "production").resolve()
        if not production.is_relative_to(root):
            raise HTTPException(status_code=409, detail="Production path escapes the project")
        if production.exists():
            for path in sorted(production.rglob("*")):
                if path.is_file():
                    resolved_path = path.resolve()
                    if not resolved_path.is_relative_to(production):
                        raise HTTPException(
                            status_code=409,
                            detail="Artifact path escapes the production directory",
                        )
                    stat = resolved_path.stat()
                    artifacts.append(
                        Artifact(
                            path=str(resolved_path.relative_to(root)),
                            name=resolved_path.name,
                            size=stat.st_size,
                            modified_at=datetime.fromtimestamp(stat.st_mtime, UTC),
                        )
                    )
        return artifacts

    @app.get("/api/v1/projects/{project_id}/artifacts/{artifact_path:path}")
    def get_artifact(project_id: str, artifact_path: str) -> FileResponse:
        project = store.get_project(project_id)
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        root = confined_project_path(project, app_settings)
        production_root = (root / "production").resolve()
        if not production_root.is_relative_to(root):
            raise HTTPException(status_code=409, detail="Production path escapes the project")
        path = (production_root / artifact_path.removeprefix("production/")).resolve()
        if not path.is_relative_to(production_root) or not path.is_file():
            raise HTTPException(status_code=404, detail="Artifact not found")
        return FileResponse(path)

    if app_settings.web_root and (app_settings.web_root / "index.html").is_file():
        assets = app_settings.web_root / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def web_app(path: str) -> FileResponse:
            requested = (app_settings.web_root / path).resolve()
            if requested.is_relative_to(app_settings.web_root) and requested.is_file():
                return FileResponse(requested)
            return FileResponse(app_settings.web_root / "index.html")

    return app


def create_project_record(store: Store, settings: Settings, payload: ProjectCreate) -> Project:
    directory = settings.workspace_root / f"{project_slug(payload.name)}-{uuid4().hex[:8]}"
    directory.mkdir(parents=True)
    manifest = {
        "schemaVersion": 1,
        "name": payload.name,
        "recipe": payload.recipe,
    }
    (directory / "video-project.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return store.create_project(payload.name, payload.recipe, directory)


app = create_app()
