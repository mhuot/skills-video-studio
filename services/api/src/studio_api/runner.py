from __future__ import annotations

import asyncio
import json
import os
import signal
from pathlib import Path

from .config import Settings
from .models import Job, JobStatus, Phase, Project
from .store import Store


class JobRunner:
    def __init__(self, store: Store, settings: Settings):
        self.store = store
        self.settings = settings
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    def enqueue(self, job: Job, project: Project) -> None:
        self._tasks[job.id] = asyncio.create_task(self._run(job, project))

    async def cancel(self, job_id: str) -> Job:
        job = self.store.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELED}:
            return job
        self.store.update_job(job_id, JobStatus.CANCEL_REQUESTED)
        process = self._processes.get(job_id)
        if process and process.returncode is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
        task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        return self.store.update_job(job_id, JobStatus.CANCELED)

    async def _run(self, job: Job, project: Project) -> None:
        try:
            async with self._semaphore:
                current = self.store.get_job(job.id)
                if not current or current.status == JobStatus.CANCELED:
                    return
                self.store.update_job(job.id, JobStatus.RUNNING)
                if job.phase == Phase.PREPARE:
                    await self._prepare(job, project)
                    self.store.update_job(job.id, JobStatus.SUCCEEDED, exit_code=0)
                    return
                await self._execute(job, project)
        except asyncio.CancelledError:
            current = self.store.get_job(job.id)
            if current and current.status != JobStatus.CANCELED:
                self.store.update_job(job.id, JobStatus.CANCELED)
        except Exception as exc:
            self.store.add_event(job.id, "error", str(exc))
            self.store.update_job(job.id, JobStatus.FAILED, error=str(exc))
        finally:
            self._processes.pop(job.id, None)
            self._tasks.pop(job.id, None)

    async def _prepare(self, job: Job, project: Project) -> None:
        project_path = self._safe_project_path(project)
        manifest = project_path / "video-project.json"
        if not manifest.is_file():
            raise FileNotFoundError("video-project.json is missing")
        payload = json.loads(manifest.read_text())
        if payload.get("recipe") != project.recipe:
            raise ValueError("Project recipe does not match its manifest")
        for directory in ("production/logs", "production/renders", "production/snapshots"):
            (project_path / directory).mkdir(parents=True, exist_ok=True)
        self.store.add_event(job.id, "log", "Project manifest verified")
        self.store.add_event(job.id, "log", "Production directories ready")

    async def _execute(self, job: Job, project: Project) -> None:
        project_path = self._safe_project_path(project)
        command = tuple(job.command)
        self.store.add_event(job.id, "command", " ".join(command))
        log_path = self.settings.data_root / "logs" / f"{job.id}.log"
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=project_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        self._processes[job.id] = process
        assert process.stdout is not None
        with log_path.open("w", encoding="utf-8") as log_file:
            async for raw_line in process.stdout:
                line = raw_line.decode(errors="replace").rstrip()
                log_file.write(line + "\n")
                log_file.flush()
                self.store.add_event(job.id, "log", line)
        exit_code = await process.wait()
        current = self.store.get_job(job.id)
        if current and current.status == JobStatus.CANCEL_REQUESTED:
            self.store.update_job(job.id, JobStatus.CANCELED, exit_code=exit_code)
        elif exit_code == 0:
            self.store.update_job(job.id, JobStatus.SUCCEEDED, exit_code=exit_code)
        else:
            self.store.update_job(
                job.id,
                JobStatus.FAILED,
                exit_code=exit_code,
                error=f"Command exited with status {exit_code}",
            )

    def _safe_project_path(self, project: Project) -> Path:
        project_path = Path(project.path).resolve()
        if not project_path.is_relative_to(self.settings.workspace_root):
            raise ValueError("Project path escapes the configured workspace")
        return project_path
