"""The API behind a real uvicorn server on a real socket, with a scripted model and no keys."""

import asyncio
import json
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from week_helpers import PLAN, SLOTS, SpyNutrition, model_for, write_catalogue_file

from meal_planner.api import create_app
from meal_planner.config import Settings

pytestmark = pytest.mark.anyio


async def test_closing_the_connection_stops_the_model_calls(tmp_path: Path) -> None:
    """A browser tab closed mid-run must not leave the model calls going."""
    release = asyncio.Event()
    model = model_for(gates={slot: release for slot in SLOTS})
    settings = Settings(catalogue_path=write_catalogue_file(tmp_path, "brinjal"))
    app = create_app(settings, model=model, nutrition=SpyNutrition("brinjal"))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    plan = json.loads(PLAN.read_text(encoding="utf-8"))

    try:
        async with asyncio.timeout(20):
            while not server.started:
                await asyncio.sleep(0.01)
            port = server.servers[0].sockets[0].getsockname()[1]
            async with (
                httpx.AsyncClient(timeout=10) as client,
                client.stream("POST", f"http://127.0.0.1:{port}/v1/week", json=plan) as response,
            ):
                assert response.status_code == 200
                async for line in response.aiter_lines():
                    if line.startswith("event: slot_started"):
                        break
            # The connection is closed now. The calls are held at the model's gate, so only a
            # cancellation can end them.
            while model.in_flight:
                await asyncio.sleep(0.01)
        assert len(model.calls) == len(SLOTS)
    finally:
        release.set()
        server.should_exit = True
        await serving


REPO = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def uvicorn_arguments_of_make_run(port: int) -> list[str]:
    """The arguments `make run` gives uvicorn, read from the Makefile with a dry run."""
    # ENV_FILE points nowhere so that the dry run never names a real .env file.
    done = subprocess.run(
        ["make", "-n", "run", "ENV_FILE=/nonexistent/.env"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    words = shlex.split(done.stdout.strip().splitlines()[-1])
    arguments = words[words.index("uvicorn") + 1 :]
    arguments[arguments.index("--port") + 1] = str(port)
    return arguments


def test_the_server_make_run_starts_does_not_log_the_query_string(tmp_path: Path) -> None:
    """uvicorn's access log writes the path with its query, which holds a plan's allergens."""
    port = free_port()
    environment = {k: v for k, v in os.environ.items() if not k.startswith("MEAL_PLANNER_")}
    environment |= {"GEMINI_API_KEY": "dummy-key-the-server-never-uses"}
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", *uvicorn_arguments_of_make_run(port)],
        cwd=tmp_path,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            assert server.poll() is None, "the server stopped before it answered"
            try:
                httpx.get(f"{base}/healthz", timeout=2).raise_for_status()
                break
            except httpx.TransportError:
                assert time.monotonic() < deadline, "the server did not answer in 60 s"
                time.sleep(0.2)
        allowed = httpx.get(f"{base}/v1/catalogue?diet=vegan&exclude_allergens=milk")
        refused = httpx.get(f"{base}/v1/catalogue?exclude_allergens=ZXQ-MARKER-3390")
        assert allowed.status_code == 200
        assert refused.status_code == 422
    finally:
        server.terminate()
        output, _ = server.communicate(timeout=30)

    assert "Uvicorn running on" in output  # the server did log, just not the requests
    assert "exclude_allergens" not in output
    assert "ZXQ-MARKER-3390" not in output
    assert "diet=vegan" not in output
