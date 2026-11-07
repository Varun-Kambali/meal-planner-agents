"""The built Docker image, called over HTTP while it runs.

CI builds the image, starts it with a placeholder model key and runs this file with
MEAL_PLANNER_IMAGE set to the tag. Everywhere else the test skips, so `make test` needs no
Docker. MEAL_PLANNER_URL says where the container answers (default http://127.0.0.1:8000).
"""

import os

import httpx
import pytest

IMAGE = os.environ.get("MEAL_PLANNER_IMAGE", "")
BASE_URL = os.environ.get("MEAL_PLANNER_URL", "http://127.0.0.1:8000")

pytestmark = pytest.mark.skipif(
    not IMAGE, reason="set MEAL_PLANNER_IMAGE to a built image tag that is running"
)


def test_container_serves_catalogue() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"status": "ok"}

        catalogue = client.get("/v1/catalogue")
        assert catalogue.status_code == 200
        rows = catalogue.json()
        assert len(rows) > 50
        assert {"id", "name", "category"} <= set(rows[0])
        assert "egg" in {row["id"] for row in rows}
