"""Running eval cases with scripted models and fake nutrition-mcp: the lines each case leaves,
when a run stops, and how finished cases and recordings are kept for --resume."""

import json
from pathlib import Path
from typing import Any

import pytest
from baseline import TASK
from cases import LabelItem, MealLabel, Selection
from pydantic import BaseModel
from reader_helpers import ReaderNutrition, reader_catalogue, reading, said
from records import CASE, ReadingCase
from runner import (
    NUTRITION_STOP,
    RATE_LIMIT_STOP,
    CaseLog,
    CaseRecorder,
    MeteredModel,
    RunStopped,
    baseline_case,
    label_kcal,
    pipeline_case,
    reading_case_run,
    run_cases,
)
from week_helpers import (
    SLOTS,
    SpyNutrition,
    canned,
    catalogue,
    changed,
    model_for,
    plan_rules,
    repair_reply,
    without_protein,
)

from meal_planner.config import Settings
from meal_planner.llm import FakeModel, ModelReply, ReplayModel
from meal_planner.nutrition import NutritionError

pytestmark = pytest.mark.anyio


class A(BaseModel):
    a: int


class Unreachable(SpyNutrition):
    async def meal(self, items: Any) -> Any:
        raise NutritionError("gone", unreachable=True)


def baseline_model(reply: dict[str, Any] | None = None) -> FakeModel:
    reply = reply or {"slots": [canned(s) for s in SLOTS]}
    return FakeModel({TASK: [json.dumps(reply)]})


async def pipeline(model: Any, nutrition: Any = None, **settings: Any) -> Any:
    return await pipeline_case(
        "office",
        plan_rules(),
        model=model,
        nutrition=nutrition or SpyNutrition(),
        catalogue=catalogue(),
        settings=Settings(**settings),
    )


async def baseline(model: Any, nutrition: Any = None) -> Any:
    return await baseline_case(
        "office",
        plan_rules(),
        model=model,
        nutrition=nutrition or SpyNutrition(),
        catalogue=catalogue(),
        settings=Settings(),
    )


# Recipe cases


async def test_a_pipeline_week_becomes_one_line_for_every_recipe_asked_for() -> None:
    line = await pipeline(model_for())

    assert line.case == "recipes/pipeline/office"
    assert len(line.recipes) == 28
    assert all(r.written and r.failures == [] and r.first_failures == [] for r in line.recipes)
    assert len(line.days) == 7
    assert line.usage.calls == 4
    assert line.seconds >= 0
    assert CASE.validate_json(CASE.dump_json(line)) == line


async def test_a_repaired_recipe_keeps_the_checks_it_failed_first() -> None:
    model = model_for(
        writes={"lunch": [changed("lunch", without_protein, "wed")]},
        repairs={"lunch": [repair_reply("lunch", "wed")]},
    )

    line = await pipeline(model)

    wednesday = next(r for r in line.recipes if (r.day, r.slot_id) == ("wed", "lunch"))
    assert wednesday.first_failures == ["slot_protein_g"]
    assert wednesday.failures == []
    assert wednesday.attempts == 2
    assert line.usage.calls == 5


async def test_a_rate_limited_pipeline_week_stops_the_run() -> None:
    with pytest.raises(RunStopped, match=RATE_LIMIT_STOP):
        await pipeline(model_for(errors={"snack": "rate_limited"}))


async def test_a_timed_out_slot_is_part_of_the_result() -> None:
    line = await pipeline(model_for(errors={"snack": "timeout"}))

    snacks = [r for r in line.recipes if r.slot_id == "snack"]
    assert all(not r.written and r.unwritten_reason == "timeout" for r in snacks)
    assert line.usage.errors == {"timeout": 1}


async def test_nutrition_mcp_out_of_reach_stops_a_pipeline_week() -> None:
    with pytest.raises(RunStopped, match=NUTRITION_STOP):
        await pipeline(model_for(), Unreachable())


async def test_a_baseline_week_is_one_call_scored_by_the_same_checks() -> None:
    line = await baseline(baseline_model())

    assert line.case == "recipes/baseline/office"
    assert line.usage.calls == 1
    assert len(line.recipes) == 28
    assert all(r.failures == [] and r.first_failures == [] for r in line.recipes)


async def test_a_baseline_recipe_that_fails_fails_first_time_too() -> None:
    reply = {"slots": [canned(s) for s in SLOTS]}
    reply["slots"][1]["recipes"][2] = without_protein(reply["slots"][1]["recipes"][2])

    line = await baseline(baseline_model(reply))

    wednesday = next(r for r in line.recipes if (r.day, r.slot_id) == ("wed", "lunch"))
    assert [f.check for f in wednesday.failures] == ["slot_protein_g"]
    assert wednesday.first_failures == ["slot_protein_g"]


async def test_a_rate_limited_baseline_stops_the_run() -> None:
    class Limited:
        async def structured(self, **kwargs: Any) -> ModelReply[Any]:
            return ModelReply(value=None, raw="", error="rate_limited")

    with pytest.raises(RunStopped, match=RATE_LIMIT_STOP):
        await baseline(Limited())


async def test_nutrition_mcp_out_of_reach_stops_a_baseline_week() -> None:
    with pytest.raises(RunStopped, match=NUTRITION_STOP):
        await baseline(baseline_model(), Unreachable())


# Reading cases


def meal_label(text: str, expected: list[dict[str, Any]]) -> MealLabel:
    return MealLabel(
        id="m99",
        text=text,
        expected=[LabelItem(**{"amount": None, "unit": None, "grams": None} | e) for e in expected],
        missing=[],
        note="",
    )


DAL_AND_ROTI = meal_label(
    "1 katori dal tadka and 2 rotis",
    [
        {"food_id": "dal_tadka", "status": "matched", "amount": 1, "unit": "katori", "grams": 150},
        {"food_id": "chapati", "status": "needs_amount"},
    ],
)


async def read(model: Any, nutrition: Any = None, label: MealLabel = DAL_AND_ROTI) -> ReadingCase:
    return await reading_case_run(
        label,
        model=model,
        nutrition=nutrition or ReaderNutrition(),
        catalogue=reader_catalogue(),
        settings=Settings(),
    )


async def test_a_read_meal_becomes_a_line_with_the_label_calories() -> None:
    model = FakeModel(
        {
            "read_meal": [
                reading(said("dal tadka", 1, "katori", "1 katori"), said("roti", 2, None, "2"))
            ]
        }
    )

    line = await read(model)

    assert line.case == "reading/m99"
    assert [(i.food_id, i.status) for i in line.items] == [
        ("dal_tadka", "matched"),
        ("chapati", "matched"),
    ]
    # dal tadka 150 g at 120 kcal per 100 g; the reader adds 2 rotis of 40 g at 300.
    assert line.label_kcal == 180.0
    assert line.kcal == 420.0
    assert line.error is None
    assert line.usage.calls == 1


async def test_a_reply_that_cannot_be_used_is_a_failed_read_not_a_stop() -> None:
    line = await read(FakeModel({"read_meal": ["not json"]}))

    assert line.error == "the model's reply could not be used (step: read_meal)"
    assert (line.items, line.kcal) == ([], None)
    assert line.usage.errors == {"invalid_json": 1}


async def test_a_timed_out_read_is_a_failed_read() -> None:
    class Slow:
        async def structured(self, **kwargs: Any) -> ModelReply[Any]:
            return ModelReply(value=None, raw="", error="timeout")

    line = await read(Slow())

    assert line.error == "the model could not be reached (step: read_meal)"


async def test_a_rate_limited_read_stops_the_run() -> None:
    class Limited:
        async def structured(self, **kwargs: Any) -> ModelReply[Any]:
            return ModelReply(value=None, raw="", error="rate_limited")

    with pytest.raises(RunStopped, match=RATE_LIMIT_STOP):
        await read(Limited())


async def test_nutrition_mcp_out_of_reach_during_a_read_stops_the_run() -> None:
    class GoneAfterLabels(ReaderNutrition):
        async def search(self, *args: Any, **kwargs: Any) -> Any:
            raise NutritionError("gone", unreachable=True)

    model = FakeModel({"read_meal": [reading(said("roti", 2, None, "2"))]})

    with pytest.raises(RunStopped, match=NUTRITION_STOP):
        await read(model, GoneAfterLabels())


async def test_label_calories_add_up_only_the_matched_labels() -> None:
    assert await label_kcal(DAL_AND_ROTI, ReaderNutrition()) == 180.0
    only_asks = meal_label("some roti", [{"food_id": "chapati", "status": "needs_amount"}])
    assert await label_kcal(only_asks, ReaderNutrition()) is None


# Counting calls


async def test_a_call_that_raises_is_counted_and_passed_on() -> None:
    class Broken:
        async def structured(self, **kwargs: Any) -> ModelReply[Any]:
            raise RuntimeError("boom")

    metered = MeteredModel(Broken())

    with pytest.raises(RuntimeError):
        await metered.structured(task="t", system="s", user="u", schema=Any, timeout_s=1)  # type: ignore[arg-type]

    assert (metered.usage.calls, metered.usage.errors) == (1, {"raised": 1})


async def test_a_reply_without_a_price_makes_the_cost_unknown() -> None:
    class Unpriced:
        async def structured(self, **kwargs: Any) -> ModelReply[Any]:
            return ModelReply(value=None, raw="{}", error="schema_mismatch", cost_usd=None)

    metered = MeteredModel(Unpriced())
    await metered.structured(task="t", system="s", user="u", schema=Any, timeout_s=1)  # type: ignore[arg-type]

    assert metered.usage.cost_unknown_calls == 1


# Keeping cases


async def test_the_log_keeps_each_case_and_reads_them_back(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    line = await baseline(baseline_model())

    CaseLog(path).append(line)

    reopened = CaseLog(path)
    assert reopened.records == [line]
    assert reopened.done == {"recipes/baseline/office"}


async def test_a_line_cut_off_mid_write_is_dropped(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    line = await baseline(baseline_model())
    CaseLog(path).append(line)
    with path.open("a") as file:
        file.write('{"kind": "recipes", "case": "recipes/pip')

    reopened = CaseLog(path)

    assert reopened.done == {"recipes/baseline/office"}
    assert path.read_text().endswith("\n")


def test_a_line_that_is_not_a_case_is_named(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text('{"kind": "recipes"}\n')

    with pytest.raises(ValueError, match="line 1"):
        CaseLog(path)


async def test_a_recording_holds_only_the_calls_of_finished_cases(tmp_path: Path) -> None:
    path, pending = tmp_path / "smoke.jsonl", tmp_path / "pending.jsonl"
    recorder = CaseRecorder(FakeModel({"t": ['{"a": 1}', '{"a": 2}', '{"a": 3}']}), path, pending)

    abandoned = recorder.start()
    await abandoned.structured(task="t", system="s", user="u", schema=A, timeout_s=1)
    finished = recorder.start()
    await finished.structured(task="t", system="s", user="u", schema=A, timeout_s=1)
    recorder.keep()

    replay = ReplayModel(path)
    reply = await replay.structured(task="t", system="s", user="u", schema=A, timeout_s=1)
    assert reply.value == A(a=2)
    assert len(path.read_text().splitlines()) == 1
    assert not pending.exists()


async def test_run_cases_skips_finished_cases_and_keeps_those_done_before_a_stop(
    tmp_path: Path,
) -> None:
    log = CaseLog(tmp_path / "cases.jsonl")
    first = await baseline(baseline_model())
    log.append(first.model_copy(update={"plan": "a", "case": "recipes/baseline/a"}))
    rules = plan_rules()

    class PipelineThenLimit:
        """Answers the four calls of plan a's pipeline week, then is rate limited."""

        def __init__(self) -> None:
            self.week = model_for()
            self.calls = 0

        async def structured(self, **kwargs: Any) -> ModelReply[Any]:
            self.calls += 1
            if self.calls > 4:
                return ModelReply(value=None, raw="", error="rate_limited")
            return await self.week.structured(**kwargs)

    said_lines: list[str] = []
    with pytest.raises(RunStopped):
        await run_cases(
            Selection(plans=["a", "b"], meals=[], slice="partial"),
            {"a": rules, "b": rules},
            {},
            model=PipelineThenLimit(),
            nutrition=SpyNutrition(),
            catalogue=catalogue(),
            settings=Settings(),
            log=log,
            say=said_lines.append,
        )

    assert [r.case for r in CaseLog(log.path).records] == [
        "recipes/baseline/a",
        "recipes/pipeline/a",
    ]
    assert len(said_lines) == 1
    assert said_lines[0].startswith("recipes/pipeline/a: 28 of 28 recipes pass, 4 calls, ")


async def test_a_label_nutrition_mcp_refuses_is_an_error_not_a_stop() -> None:
    unknown = meal_label(
        "1 bowl of something",
        [{"food_id": "no_such_food", "status": "matched", "amount": 1, "unit": "g", "grams": 10}],
    )

    with pytest.raises(NutritionError):
        await label_kcal(unknown, ReaderNutrition())
