"""Settings, read from environment variables prefixed MEAL_PLANNER_."""

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MEAL_PLANNER_")

    # The model that writes recipes and reads typed meals (LiteLLM id; the default is free tier).
    model: str = "gemini/gemini-3.1-flash-lite"

    # Recipe checks: calories within this fraction of the slot's share, protein at least this
    # fraction of its share.
    recipe_kcal_tolerance: float = Field(default=0.10, gt=0, le=1)
    recipe_protein_min_share: float = Field(default=0.90, gt=0, le=1)
    # Day check: calories within this fraction of the day type's target.
    day_kcal_tolerance: float = Field(default=0.05, gt=0, le=1)
    # How many times a failing recipe goes back to the model.
    max_repair_attempts: int = Field(default=3, ge=0)
    # Variety: two recipes in one slot whose ingredient sets overlap by this Jaccard similarity
    # or more fail. The value is the lowest similarity that fails, not the highest that passes.
    variety_jaccard_fail_at: float = Field(default=0.6, gt=0, le=1)
    # No waste: an ingredient that keeps fewer than this many days once opened, and of which the
    # week uses less than this share of a usual pack, is flagged.
    no_waste_keeps_days: int = Field(default=7, ge=1)
    no_waste_min_pack_share: float = Field(default=0.5, gt=0, le=1)

    # Writing a week: the most model calls in flight at once. Every slot is written in parallel,
    # and without a cap they would all hit a free-tier rate limit together and retry in step.
    max_concurrent_model_calls: int = Field(default=4, ge=1)
    # Seconds one slot's call may take. A slot is seven recipes, so this is longer than a chat turn.
    slot_call_timeout_s: float = Field(default=120, gt=0)

    # Fitting portions: when a recipe's calories are outside the slot's band, code scales every
    # gram by the same factor to bring them to the slot's share, if the factor is within these
    # bounds and no ingredient passes 1000 g. Outside them the writer is asked to redo the amounts.
    fit_portions: bool = True
    portion_scale_min: float = Field(default=0.5, gt=0, le=1)
    portion_scale_max: float = Field(default=2.0, ge=1)

    # What the writer is shown: each ingredient's nutrition per 100 g, and the slot's share of the
    # day's saturated-fat cap, fibre floor and extra targets. Off, it sees names and calorie bands
    # only, as before. Either way no number it writes is used.
    writer_nutrient_hints: bool = True

    # Seconds one call to read a typed meal may take. A call reads a sentence or two, so this is
    # shorter than a slot's.
    reader_call_timeout_s: float = Field(default=30, gt=0)

    # Input bounds.
    max_meal_text_chars: int = Field(default=500, ge=1)
    max_body_bytes: int = Field(default=65536, ge=1)
    # Replacing a recipe sends the rest of the week back (up to 56 drafts: 7 days of at most 8
    # slots), so its bound is larger.
    max_replace_body_bytes: int = Field(default=131072, ge=1)

    # Requests one client address may make to the /v1 routes in a minute; over it, 429.
    rate_limit_per_minute: int = Field(default=10, ge=1)
    # Browser origins allowed to call the API, separated by commas. Empty adds no CORS headers.
    cors_origins: Annotated[list[str], NoDecode] = []

    # Extra nutrition-mcp dataset folders, separated as in PATH, as nutrition-mcp reads them.
    # The default is this project's food file, which the catalogue needs; empty means
    # nutrition-mcp's bundled data only.
    nutrition_data_dirs: str = str(REPO_ROOT / "data" / "foods")
    # The ingredients the writer may use. Both paths are relative to a source checkout; set the
    # variables where the repository is not on disk.
    catalogue_path: Path = REPO_ROOT / "data" / "catalogue.yaml"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        # NoDecode keeps pydantic-settings from reading the variable as JSON.
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
