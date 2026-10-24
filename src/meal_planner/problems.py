"""Invalid input, said the same way by the API and the command line.

A problem is where it is and what is wrong, and nothing else. Pydantic's own error also holds
the value that was rejected, and the rules hold what the user typed, so that value stays out of
every response and every log.
"""

from collections.abc import Iterable, Mapping
from typing import Any, TypedDict

from meal_planner.catalogue import Catalogue, check_rules_against
from meal_planner.rules import PlanRules


class Problem(TypedDict):
    loc: list[str | int]
    msg: str


def problems_from(errors: Iterable[Mapping[str, Any]]) -> list[Problem]:
    """Pydantic or FastAPI validation errors, cut down to where and what."""
    return [{"loc": list(error["loc"]), "msg": str(error["msg"])} for error in errors]


def unknown_foods(
    rules: PlanRules, catalogue: Catalogue, *, under: tuple[str, ...] = ("body",)
) -> list[Problem]:
    """A problem for each excluded or liked food the catalogue does not have.

    `under` is where the rules sit in the input, so that the location points at the right field.
    """
    found: list[Problem] = []
    for message in check_rules_against(rules, catalogue):
        # "food.liked_foods: 'x' is not in the catalogue": the field path, then what is wrong.
        field, _, text = message.partition(": ")
        found.append({"loc": [*under, *field.split(".")], "msg": text})
    return found


def where(problem: Problem) -> str:
    """The location as a dotted path, such as slots.0.kcal_share."""
    return ".".join(str(part) for part in problem["loc"])
