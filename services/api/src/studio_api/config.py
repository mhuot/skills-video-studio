from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    workspace_root: Path
    data_root: Path
    web_root: Path | None
    engine_version: str
    max_concurrent_jobs: int
    seed_demo: bool

    @classmethod
    def from_env(cls) -> Settings:
        web_root_value = os.getenv("STUDIO_WEB_ROOT")
        local_root = Path.cwd() / ".studio"
        return cls(
            workspace_root=Path(
                os.getenv("STUDIO_WORKSPACE_ROOT", local_root / "workspace")
            ).resolve(),
            data_root=Path(os.getenv("STUDIO_DATA_ROOT", local_root / "data")).resolve(),
            web_root=Path(web_root_value).resolve() if web_root_value else None,
            engine_version=os.getenv("STUDIO_ENGINE_VERSION", "0.3.0"),
            max_concurrent_jobs=max(1, int(os.getenv("STUDIO_MAX_CONCURRENT_JOBS", "1"))),
            seed_demo=os.getenv("STUDIO_SEED_DEMO", "").lower() in {"1", "true", "yes"},
        )

    def initialize(self) -> None:
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.data_root.mkdir(parents=True, exist_ok=True)
        (self.data_root / "logs").mkdir(parents=True, exist_ok=True)
