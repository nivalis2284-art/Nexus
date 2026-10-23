"""The skill editor uses the same source roots as chat discovery."""

from pathlib import Path
from typing import Any

from ..skill_management import SkillManager


def manager(state: dict[str, Any]) -> SkillManager:
    config = state["store"]
    return SkillManager(
        config.home,
        Path(config.load().workspace),
        Path(__file__).parents[1] / "builtin_skills",
    )
