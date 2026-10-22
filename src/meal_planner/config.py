"""Settings, read from environment variables prefixed MEAL_PLANNER_."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    # Input bounds.
    max_meal_text_chars: int = Field(default=500, ge=1)
    max_body_bytes: int = Field(default=65536, ge=1)

    # Extra nutrition-mcp dataset folders, as nutrition-mcp reads them. Empty means its bundled
    # data only.
    nutrition_data_dirs: str = ""
    # The ingredients the writer may use. Relative to a source checkout; set the variable
    # where the repository is not on disk.
    catalogue_path: Path = REPO_ROOT / "data" / "catalogue.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()
