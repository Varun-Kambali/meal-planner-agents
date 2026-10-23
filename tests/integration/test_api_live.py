"""The API behind a real uvicorn server on a real socket, with a scripted model and no keys."""

import asyncio
import json
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
