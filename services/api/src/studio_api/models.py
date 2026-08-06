from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Phase(StrEnum):
    PREPARE = "prepare"
    NARRATE = "narrate"
    MEASURE = "measure"
    COMPOSE = "compose"
    VALIDATE = "validate"
    SNAPSHOT = "snapshot"
    RENDER = "render"
    QA = "qa"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELED = "canceled"


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    recipe: str = Field(default="explainer-video", pattern=r"^[a-z0-9-]+$")


class Project(BaseModel):
    id: str
    name: str
    recipe: str
    path: str
    created_at: datetime


class PhaseState(BaseModel):
    phase: Phase
    status: JobStatus | None
    job_id: str | None = None
    updated_at: datetime | None = None


class ProjectDetail(Project):
    phases: list[PhaseState]


class JobCreate(BaseModel):
    phase: Phase


class Job(BaseModel):
    id: str
    project_id: str
    phase: Phase
    status: JobStatus
    command: list[str]
    engine_version: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    exit_code: int | None = None
    error: str | None = None


class JobEvent(BaseModel):
    id: int
    job_id: str
    kind: str
    message: str
    created_at: datetime


class Artifact(BaseModel):
    path: str
    name: str
    size: int
    modified_at: datetime


class Health(BaseModel):
    status: str
    engine_version: str
    platform: str
