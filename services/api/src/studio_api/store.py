from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import uuid4

from .models import Job, JobEvent, JobStatus, Phase, PhaseState, Project, ProjectDetail


def utc_now() -> datetime:
    return datetime.now(UTC)


def serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class Store:
    def __init__(self, path: Path):
        self.path = path
        self._lock = Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    recipe TEXT NOT NULL,
                    path TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                    phase TEXT NOT NULL,
                    status TEXT NOT NULL,
                    command_json TEXT NOT NULL,
                    engine_version TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    exit_code INTEGER,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS jobs_project_phase
                    ON jobs(project_id, phase, created_at DESC);
                CREATE INDEX IF NOT EXISTS events_job
                    ON events(job_id, id);
                """
            )

    def create_project(self, name: str, recipe: str, path: Path) -> Project:
        project = Project(
            id=str(uuid4()),
            name=name,
            recipe=recipe,
            path=str(path),
            created_at=utc_now(),
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO projects(id, name, recipe, path, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    project.id,
                    project.name,
                    project.recipe,
                    project.path,
                    project.created_at.isoformat(),
                ),
            )
        return project

    def list_projects(self) -> list[Project]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
        return [self._project_from_row(row) for row in rows]

    def get_project(self, project_id: str) -> Project | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return self._project_from_row(row) if row else None

    def get_project_detail(self, project_id: str) -> ProjectDetail | None:
        project = self.get_project(project_id)
        if not project:
            return None
        phases: list[PhaseState] = []
        with self._connect() as connection:
            for phase in Phase:
                row = connection.execute(
                    """
                    SELECT id, status, COALESCE(finished_at, started_at, created_at) AS updated_at
                    FROM jobs WHERE project_id = ? AND phase = ?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (project_id, phase.value),
                ).fetchone()
                phases.append(
                    PhaseState(
                        phase=phase,
                        status=JobStatus(row["status"]) if row else None,
                        job_id=row["id"] if row else None,
                        updated_at=datetime.fromisoformat(row["updated_at"]) if row else None,
                    )
                )
        return ProjectDetail(**project.model_dump(), phases=phases)

    def create_job(
        self,
        project_id: str,
        phase: Phase,
        command: tuple[str, ...],
        engine_version: str,
    ) -> Job:
        job = Job(
            id=str(uuid4()),
            project_id=project_id,
            phase=phase,
            status=JobStatus.QUEUED,
            command=list(command),
            engine_version=engine_version,
            created_at=utc_now(),
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs(
                    id, project_id, phase, status, command_json, engine_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.project_id,
                    job.phase.value,
                    job.status.value,
                    json.dumps(job.command),
                    job.engine_version,
                    job.created_at.isoformat(),
                ),
            )
        self.add_event(job.id, "status", "Job queued")
        return job

    def get_job(self, job_id: str) -> Job | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_from_row(row) if row else None

    def update_job(
        self,
        job_id: str,
        status: JobStatus,
        *,
        exit_code: int | None = None,
        error: str | None = None,
    ) -> Job:
        now = utc_now()
        with self._lock, self._connect() as connection:
            current = connection.execute(
                "SELECT started_at FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
            if not current:
                raise KeyError(job_id)
            started_at = current["started_at"]
            if status == JobStatus.RUNNING and not started_at:
                started_at = now.isoformat()
            finished_at = (
                now.isoformat()
                if status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELED}
                else None
            )
            connection.execute(
                """
                UPDATE jobs SET status = ?, started_at = ?, finished_at = COALESCE(?, finished_at),
                    exit_code = COALESCE(?, exit_code), error = COALESCE(?, error)
                WHERE id = ?
                """,
                (status.value, started_at, finished_at, exit_code, error, job_id),
            )
        self.add_event(job_id, "status", status.value)
        job = self.get_job(job_id)
        if not job:
            raise KeyError(job_id)
        return job

    def add_event(self, job_id: str, kind: str, message: str) -> JobEvent:
        created_at = utc_now()
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO events(job_id, kind, message, created_at) VALUES (?, ?, ?, ?)",
                (job_id, kind, message, created_at.isoformat()),
            )
            event_id = cursor.lastrowid
        return JobEvent(
            id=event_id,
            job_id=job_id,
            kind=kind,
            message=message,
            created_at=created_at,
        )

    def list_events(self, job_id: str, after_id: int = 0) -> list[JobEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events WHERE job_id = ? AND id > ? ORDER BY id",
                (job_id, after_id),
            ).fetchall()
        return [
            JobEvent(
                id=row["id"],
                job_id=row["job_id"],
                kind=row["kind"],
                message=row["message"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        ]

    @staticmethod
    def _project_from_row(row: sqlite3.Row) -> Project:
        return Project(
            id=row["id"],
            name=row["name"],
            recipe=row["recipe"],
            path=row["path"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _job_from_row(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            project_id=row["project_id"],
            phase=Phase(row["phase"]),
            status=JobStatus(row["status"]),
            command=json.loads(row["command_json"]),
            engine_version=row["engine_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            finished_at=(
                datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
            ),
            exit_code=row["exit_code"],
            error=row["error"],
        )
