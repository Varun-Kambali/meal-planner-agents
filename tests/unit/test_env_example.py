"""`.env.example` lists every setting in config.py, so a reader can find how to change one."""

import re
from pathlib import Path

from pydantic import AliasChoices

from meal_planner.config import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"

# Variables the example lists that are not settings of the app: the model provider's key, which
# LiteLLM reads, and the key the food build script reads.
NOT_SETTINGS = {"GEMINI_API_KEY", "USDA_FDC_API_KEY"}


def setting_variables() -> set[str]:
    """The environment name of each setting: Langfuse's own name where it has one (its first,
    when it has several), and MEAL_PLANNER_ and the field's name in capitals otherwise."""
    names: set[str] = set()
    for name, field in Settings.model_fields.items():
        alias = field.validation_alias
        if isinstance(alias, AliasChoices):
            first = alias.choices[0]
            assert isinstance(first, str)
            names.add(first)
        elif isinstance(alias, str):
            names.add(alias)
        else:
            names.add(f"MEAL_PLANNER_{name.upper()}")
    return names


def listed_variables() -> set[str]:
    """The variables set in .env.example, or set there in a comment as an option."""
    pattern = re.compile(r"^(?:# ?)?([A-Z][A-Z0-9_]*)=", re.MULTILINE)
    return set(pattern.findall(ENV_EXAMPLE.read_text(encoding="utf-8")))


def test_every_setting_is_listed_in_env_example() -> None:
    assert setting_variables() - listed_variables() == set()


def test_env_example_lists_no_variable_the_app_does_not_read() -> None:
    assert listed_variables() - setting_variables() - NOT_SETTINGS == set()


def test_the_repair_limit_is_listed_with_the_api_cap_it_depends_on() -> None:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    at = lines.index("# MEAL_PLANNER_MAX_REPAIR_ATTEMPTS=3")
    comment: list[str] = []
    for line in reversed(lines[:at]):
        if not line.startswith("#"):
            break
        comment.append(line)

    assert "api.MAX_ATTEMPTS" in " ".join(comment)
