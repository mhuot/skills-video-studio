from pathlib import Path
from time import sleep

from fastapi.testclient import TestClient
from studio_api.config import Settings
from studio_api.main import create_app


def client_for(tmp_path: Path) -> TestClient:
    settings = Settings(
        workspace_root=tmp_path / "workspace",
        data_root=tmp_path / "data",
        web_root=None,
        engine_version="test-engine",
        max_concurrent_jobs=1,
        seed_demo=False,
    )
    return TestClient(create_app(settings))


def test_create_project_and_prepare(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        response = client.post(
            "/api/v1/projects",
            json={"name": "Demo project", "recipe": "explainer-video"},
        )
        assert response.status_code == 201
        project = response.json()
        manifest = Path(project["path"]) / "video-project.json"
        assert manifest.is_file()

        response = client.post(
            f"/api/v1/projects/{project['id']}/jobs",
            json={"phase": "prepare"},
        )
        assert response.status_code == 202
        job_id = response.json()["id"]

        for _ in range(50):
            job = client.get(f"/api/v1/jobs/{job_id}").json()
            if job["status"] == "succeeded":
                break
            sleep(0.01)
        assert job["status"] == "succeeded"
        detail = client.get(f"/api/v1/projects/{project['id']}").json()
        assert detail["phases"][0]["status"] == "succeeded"


def test_rejects_unsupported_recipe(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        response = client.post(
            "/api/v1/projects",
            json={"name": "Unsafe", "recipe": "arbitrary-shell"},
        )
        assert response.status_code == 422


def test_recipe_catalog_only_advertises_released_adapters(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        recipes = client.get("/api/v1/recipes").json()
        assert [recipe["id"] for recipe in recipes] == ["explainer-video"]


def test_artifact_path_cannot_escape_project(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        project = client.post(
            "/api/v1/projects",
            json={"name": "Demo", "recipe": "explainer-video"},
        ).json()
        response = client.get(
            f"/api/v1/projects/{project['id']}/artifacts/../../video-project.json"
        )
        assert response.status_code == 404


def test_artifact_listing_rejects_escaping_production_symlink(tmp_path: Path) -> None:
    with client_for(tmp_path) as client:
        project = client.post(
            "/api/v1/projects",
            json={"name": "Demo", "recipe": "explainer-video"},
        ).json()
        external = tmp_path / "external"
        external.mkdir()
        (external / "secret.txt").write_text("not an artifact")
        (Path(project["path"]) / "production").symlink_to(external, target_is_directory=True)

        response = client.get(f"/api/v1/projects/{project['id']}/artifacts")
        assert response.status_code == 409
