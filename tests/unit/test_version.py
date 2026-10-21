import re

import meal_planner


def test_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", meal_planner.__version__)
