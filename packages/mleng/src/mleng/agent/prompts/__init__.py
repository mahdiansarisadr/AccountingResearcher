"""Loader for prompt content stored as markdown alongside this module.

Keeps prompt *content* in `.md` files (easy to edit/diff/version) and this thin
*code* layer just reads and fills runtime placeholders (e.g. today's date).
"""

from __future__ import annotations

from datetime import date
from importlib.resources import files


def _read(name: str) -> str:
    return files(__package__).joinpath(name).read_text(encoding="utf-8")


def get_system_prompt() -> str:
    """The agent's system prompt, with runtime placeholders filled."""
    template = _read("system.md")
    return template.replace("{today}", date.today().isoformat())
