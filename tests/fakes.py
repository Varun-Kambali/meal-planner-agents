"""Stand-ins for the app's services, for unit tests. Import them with `from fakes import ...`."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from nutrition_mcp.models import Diet, Kind, Nutrients
from nutrition_mcp.nutrients import NUTRIENT_KEYS
from nutrition_mcp.nutrition import glycemic_load, scale, sum_nutrients
from nutrition_mcp.queries import MAX_MEAL_ITEMS
from nutrition_mcp.schemas import (
    DECIMALS,
    FoodSummary,
    MealItemIn,
    MealResult,
    NutritionResult,
    SearchResult,
)
from nutrition_mcp.search import (
    FILTERED_NOT_FOUND_NOTE,
    MAX_QUERY_LENGTH,
    MAX_SEARCH_LIMIT,
    NOT_FOUND_NOTE,
    PLURAL_MIN_LETTERS,
    normalise,
)
from nutrition_mcp.units import MAX_GRAMS, normalise_unit

from meal_planner.nutrition import NutritionError, NutritionPort, refusal

# Spellings of a unit that nutrition-mcp accepts, each with the unit it means: it strips and
# lowercases, reads gram, grams and gm as g, and tries a plural without its "s". The fake and a
# live test both run through this table, so they stay in step.
UNIT_SPELLINGS = {
    "Rotis": "roti",
    " ROTI ": "roti",
    "pieces": "piece",
    "grams": "g",
    " G": "g",
    "Gm": "g",
}


def _find_unit(units: Mapping[str, float], unit: str) -> str | None:
    """The unit the server would read `unit` as, or None. Mirrors nutrition-mcp's _find_unit."""
    word = normalise_unit(unit)
    for candidate in (word, word.removesuffix("s"), word.removesuffix("es")):
        if candidate in units:
            return candidate
    return None


def _show_grams(grams: float) -> str:
    return f"{round(grams, 1):g}"


def _rounded(nutrients: Nutrients) -> Nutrients:
    return Nutrients.model_validate(
        {key: None if value is None else round(value, DECIMALS) for key, value in nutrients}
    )


def _schema_refusal(
    tool: str, field: str, problem: str, kind: str, value: Any, food_id: str | None = None
) -> NutritionError:
    """What the SDK sends when an argument is outside its schema, before the tool runs.

    Pydantic writes the message, so it names the argument and the value and no food. The real
    one ends with a link to Pydantic's documentation; that line is left out.
    """
    shown = f"input_value={value!r}, input_type={type(value).__name__}"
    message = f"1 validation error for {tool}Arguments\n{field}\n  {problem} [type={kind}, {shown}]"
    return refusal(message, food_id)


def _positive_amount(tool: str, field: str, amount: float, food_id: str | None) -> None:
    # Written as "not >" so that NaN is refused along with zero and negatives.
    if not amount > 0:
        raise _schema_refusal(
            tool, field, "Input should be greater than 0", "greater_than", amount, food_id
        )


def _total_incomplete(
    results: list[NutritionResult], lacking: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Mirrors nutrition-mcp's: a dish names the ingredients it lacks, an ingredient itself."""
    named: dict[str, list[str]] = {}
    for key in NUTRIENT_KEYS:
        names: list[str] = []
        for result in results:
            own = result.incomplete.get(key)
            if own:
                names.extend(own)
            elif result.food.id in lacking.get(key, ()):
                names.append(result.food.id)
        if names:
            named[key] = list(dict.fromkeys(names))
    return named


class FakeNutrition:
    """nutrition-mcp without the server: real result models, built from per-100 g values.

    `per_100g` maps a food id to its nutrients per 100 g, using nutrition-mcp's nutrient names
    (kcal, protein_g, carbs_available_g ...). A nutrient a food leaves out is reported as
    missing, the way the server reports one. Every food takes grams; `units` adds the others a
    test needs, as grams in one of each. Units are read as the server reads them (see
    UNIT_SPELLINGS), so a repair path a test exercises is one production can reach. A food is a
    vegetarian ingredient with no glycemic index unless `diets`, `kinds` or `gi` say otherwise.

    A food is called its id with spaces and a capital unless `names` says otherwise, and has no
    aliases unless `aliases` gives some. A dish lacks nutrients when `incomplete` says which, by
    the ingredient it lacks them for. Calls refuse what the server refuses, in its words.

    Where it differs from the server: search matches whole words only (the server also accepts
    a word one letter off) and suggests no close names; `raw_ingredient_totals` is always empty;
    an unknown food id is reported without the server's list of close matches.
    """

    def __init__(
        self,
        per_100g: Mapping[str, Mapping[str, float]],
        *,
        units: Mapping[str, Mapping[str, float]] | None = None,
        diets: Mapping[str, Diet] | None = None,
        kinds: Mapping[str, Kind] | None = None,
        gi: Mapping[str, float] | None = None,
        names: Mapping[str, str] | None = None,
        aliases: Mapping[str, list[str]] | None = None,
        incomplete: Mapping[str, Mapping[str, list[str]]] | None = None,
    ) -> None:
        self._per_100g = per_100g
        self._units = units or {}
        self._diets = diets or {}
        self._kinds = kinds or {}
        self._gi = gi or {}
        self._names = names or {}
        self._aliases = aliases or {}
        self._incomplete = incomplete or {}

    def _summary(self, food_id: str) -> FoodSummary:
        return FoodSummary(
            id=food_id,
            kind=self._kinds.get(food_id, "ingredient"),
            name=self._names.get(food_id, food_id.replace("_", " ").capitalize()),
            aliases=list(self._aliases.get(food_id, [])),
            regions=[],
            diet=self._diets.get(food_id, "veg"),
            source_ids=["fake"],
            units={"g": 1.0, **self._units.get(food_id, {})},
        )

    async def search(
        self,
        query: str,
        kind: Literal["dish", "ingredient"] | None = None,
        limit: int = 5,
    ) -> SearchResult:
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            problem, kind_ = (
                ("Input should be greater than or equal to 1", "greater_than_equal")
                if limit < 1
                else (
                    f"Input should be less than or equal to {MAX_SEARCH_LIMIT}",
                    "less_than_equal",
                )
            )
            raise _schema_refusal("search_food", "limit", problem, kind_, limit)
        if len(query) > MAX_QUERY_LENGTH:
            problem = f"String should have at most {MAX_QUERY_LENGTH} characters"
            raise _schema_refusal("search_food", "query", problem, "string_too_long", query)
        wanted = normalise(query)
        if not wanted:
            raise refusal("query must contain at least one letter or digit")

        foods = [f for f in self._per_100g if kind in (None, self._kinds.get(f, "ingredient"))]
        # A query that finds nothing is tried again in the singular, as the server does.
        singular = " ".join(
            word.removesuffix("s") if len(word) >= PLURAL_MIN_LETTERS else word
            for word in wanted.split()
        )
        matches = self._matching(wanted, foods) or self._matching(singular, foods)
        note = FILTERED_NOT_FOUND_NOTE if kind is not None else NOT_FOUND_NOTE
        return SearchResult(
            query=query,
            results=[self._summary(food_id) for food_id in matches[:limit]],
            total=len(matches),
            suggestions=[],
            note=None if matches else note,
        )

    def _matching(self, wanted: str, food_ids: list[str]) -> list[str]:
        """The foods with a name, alias or id that holds every word of `wanted`, exact ones first.

        `wanted` is normalised. A word has to equal a word of the name; part of one is no match.
        """
        words = set(wanted.split())
        found: list[tuple[bool, str]] = []
        for food_id in food_ids:
            summary = self._summary(food_id)
            names = [normalise(n) for n in (summary.name, food_id, *summary.aliases)]
            if any(words <= set(name.split()) for name in names):
                found.append((wanted not in names, food_id))
        # The sort is stable, so foods that match alike stay in the order they were given.
        found.sort(key=lambda pair: pair[0])
        return [food_id for _, food_id in found]

    async def nutrition(self, food_id: str, amount: float, unit: str) -> NutritionResult:
        _positive_amount("get_nutrition", "amount", amount, food_id)
        return self._portion(food_id, amount, unit)[0]

    def _portion(self, food_id: str, amount: float, unit: str) -> tuple[NutritionResult, Nutrients]:
        """The result, and the nutrients before rounding, which is what a meal adds up."""
        if food_id not in self._per_100g:
            message = f"unknown food id '{food_id}'; search_food finds food ids by name"
            raise refusal(message, food_id)
        summary = self._summary(food_id)
        name = _find_unit(summary.units, unit)
        if name is None:
            valid = ", ".join(summary.units)
            raise refusal(f"unknown unit '{unit}' for {food_id}; valid units: {valid}", food_id)

        grams = amount * summary.units[name]
        if grams > MAX_GRAMS:
            message = (
                f"{amount:g} {name} comes to {_show_grams(grams)} g, "
                f"over the limit of {MAX_GRAMS:g} g"
            )
            raise refusal(message, food_id)
        exact = scale(Nutrients.model_validate(self._per_100g[food_id]), grams)
        incomplete = {key: list(names) for key, names in self._incomplete.get(food_id, {}).items()}
        gi = self._gi.get(food_id)
        result = NutritionResult(
            food=summary,
            amount=amount,
            unit=unit,
            grams=round(grams, DECIMALS),
            measure_assumption=(
                f"{_show_grams(grams)} g"
                if name == "g"
                else f"{amount:g} {name} = {_show_grams(grams)} g"
            ),
            nutrients=_rounded(exact),
            incomplete=incomplete,
            raw_ingredient_totals=[],
            gi=gi,
            gi_ref=None,
            # A partial carbohydrate sum would understate the load, so it is left out.
            glycemic_load=(
                None
                if "carbs_available_g" in incomplete
                else glycemic_load(gi, exact.carbs_available_g)
            ),
            oil_assumption=None,
            sources=["fake"],
        )
        return result, exact

    async def meal(self, items: list[MealItemIn]) -> MealResult:
        if not 1 <= len(items) <= MAX_MEAL_ITEMS:
            problem, kind = (
                ("List should have at least 1 item after validation, not 0", "too_short")
                if not items
                else (
                    f"List should have at most {MAX_MEAL_ITEMS} items after validation, "
                    f"not {len(items)}",
                    "too_long",
                )
            )
            raise _schema_refusal("compute_meal", "items", problem, kind, list(items))
        for index, item in enumerate(items):
            _positive_amount("compute_meal", f"items.{index}.amount", item.amount, None)
        portions: list[tuple[NutritionResult, Nutrients]] = []
        for number, item in enumerate(items, start=1):
            try:
                portions.append(self._portion(item.food_id, item.amount, item.unit))
            except NutritionError as err:
                # The server names the item that failed; the food is read back from that.
                raise refusal(f"item {number} ({item.food_id}): {err}") from err
        results = [result for result, _ in portions]
        total, lacking = sum_nutrients([(result.food.id, exact) for result, exact in portions])
        loads = [result.glycemic_load for result in results]
        known = [load for load in loads if load is not None]
        return MealResult(
            items=results,
            total=_rounded(total),
            total_incomplete=_total_incomplete(results, lacking),
            glycemic_load=round(sum(known), 1) if known else None,
            glycemic_load_missing=[
                result.food.id for result, load in zip(results, loads, strict=True) if load is None
            ],
        )


if TYPE_CHECKING:
    # mypy checks the fake against the port it stands in for. Nothing runs.
    _port: NutritionPort = FakeNutrition({})
