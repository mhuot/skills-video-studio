from __future__ import annotations

from dataclasses import dataclass

from .models import Phase


@dataclass(frozen=True)
class Operation:
    phase: Phase
    description: str
    command: tuple[str, ...]


EXPLAINER_OPERATIONS = {
    Phase.PREPARE: Operation(
        Phase.PREPARE,
        "Verify the project manifest, recipe, source files, and output directories.",
        ("studio-internal", "prepare"),
    ),
    Phase.NARRATE: Operation(
        Phase.NARRATE,
        "Generate local narration using the project's trusted TTS script.",
        ("python", "tools/tts_generate.py"),
    ),
    Phase.MEASURE: Operation(
        Phase.MEASURE,
        "Measure narration and derive the scene timeline.",
        ("python", "tools/measure_audio.py"),
    ),
    Phase.COMPOSE: Operation(
        Phase.COMPOSE,
        "Check the browser composition before validation.",
        ("hyperframes", "check", "video"),
    ),
    Phase.VALIDATE: Operation(
        Phase.VALIDATE,
        "Run the project's validation ladder.",
        ("hyperframes", "validate", "video"),
    ),
    Phase.SNAPSHOT: Operation(
        Phase.SNAPSHOT,
        "Render representative frames and a visual contact sheet.",
        ("hyperframes", "snapshot", "video", "--output", "production/snapshots"),
    ),
    Phase.RENDER: Operation(
        Phase.RENDER,
        "Render the approved composition to the final master.",
        (
            "hyperframes",
            "render",
            "video",
            "--output",
            "production/renders/master.mp4",
        ),
    ),
    Phase.QA: Operation(
        Phase.QA,
        "Inspect the final master and record its media properties.",
        (
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "production/renders/master.mp4",
        ),
    ),
}

RECIPES = {
    "explainer-video": EXPLAINER_OPERATIONS,
}


def get_operation(recipe: str, phase: Phase) -> Operation:
    try:
        return RECIPES[recipe][phase]
    except KeyError as exc:
        raise ValueError(f"Unsupported recipe operation: {recipe}/{phase}") from exc
