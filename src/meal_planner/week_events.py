"""The events a week run reports as it goes, and the week it ends with."""

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_serializer,
    field_validator,
)

from meal_planner.recipes import CheckedRecipe, DayReport, Failure
from meal_planner.rules import DayKey

# Events


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SlotStarted(_Event):
    """A model call to write recipes for a slot has begun. `attempt` counts the slot's calls."""

    kind: Literal["slot_started"] = "slot_started"
    slot_id: str
    days: list[DayKey]
    attempt: int


class SlotWritten(_Event):
    """The model's reply for the slot had the recipes asked for. They have not been checked."""

    kind: Literal["slot_written"] = "slot_written"
    slot_id: str
    days: list[DayKey]
    attempt: int


class RecipeChecked(_Event):
    """A recipe was checked, or its failures changed. `attempt` counts this recipe's tries."""

    kind: Literal["recipe_checked"] = "recipe_checked"
    day: DayKey
    slot_id: str
    attempt: int
    failures: list[Failure]


class RepairStarted(_Event):
    """A model call to rewrite a slot's failing recipes has begun."""

    kind: Literal["repair_started"] = "repair_started"
    slot_id: str
    days: list[DayKey]
    attempt: int


class SlotFailed(_Event):
    """A model call gave nothing usable. `final` is false when the slot will be tried again."""

    kind: Literal["slot_failed"] = "slot_failed"
    slot_id: str
    days: list[DayKey]
    attempt: int
    reason: str
    final: bool


class _NoWasteRow(BaseModel):
    ingredient: str
    window_start: DayKey
    failure: Failure


class WeekResult(BaseModel):
    """The week as the graph leaves it: every recipe with its numbers, and what still fails."""

    model_config = ConfigDict(extra="forbid")

    recipes: list[CheckedRecipe]
    days: list[DayReport]
    # Keyed by the ingredient and the first day of the shopping window. These are warnings: no
    # recipe is to blame, so nothing is repaired for them.
    no_waste: dict[tuple[str, DayKey], Failure]
    # (day, slot id, reason) for each recipe asked for and not delivered. For a replacement the
    # old recipe stays in `recipes`.
    unwritten: list[tuple[DayKey, str, str]]
    calls: int
    input_tokens: int
    output_tokens: int
    # None when the model has no price in LiteLLM's list.
    cost_usd: float | None

    # JSON has no tuple keys, so the warnings travel as a list of rows and come back as a dict.
    @field_validator("no_waste", mode="before")
    @classmethod
    def _rows_to_dict(cls, value: Any) -> Any:
        if isinstance(value, list):
            rows = [_NoWasteRow.model_validate(row) for row in value]
            return {(row.ingredient, row.window_start): row.failure for row in rows}
        return value

    @field_serializer("no_waste", when_used="json")
    def _dict_to_rows(self, value: dict[tuple[str, DayKey], Failure]) -> list[_NoWasteRow]:
        return [
            _NoWasteRow(ingredient=food_id, window_start=start, failure=failure)
            for (food_id, start), failure in value.items()
        ]


class WeekDone(_Event):
    """The last event of every run."""

    kind: Literal["week_done"] = "week_done"
    week: WeekResult


WeekEvent = Annotated[
    SlotStarted | SlotWritten | RecipeChecked | RepairStarted | SlotFailed | WeekDone,
    Field(discriminator="kind"),
]
EVENTS: TypeAdapter[WeekEvent] = TypeAdapter(WeekEvent)
