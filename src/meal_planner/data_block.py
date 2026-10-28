"""The parts of a prompt every model call shares: its template, and the quoting of data.

A prompt puts what the user wrote or set between <data> and </data>. Each value goes in as a
quoted string, so text that holds "</data>" cannot close the block and pass for an instruction.
The writer, the repair prompt, the eval's baseline and the meal reader all build their prompts
with these two. Tracing never reads a prompt apart: every prompt is masked whole (tracing.py).
"""

import json
from functools import cache
from importlib import resources


@cache
def template(name: str) -> str:
    """The text of a file in `prompts/`."""
    return resources.files("meal_planner").joinpath("prompts", name).read_text(encoding="utf-8")


def quote(text: str) -> str:
    """The text as a JSON string with angle brackets escaped, so it cannot close the data block."""
    return json.dumps(text, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
