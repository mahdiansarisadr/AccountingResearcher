"""Loader for prompt content stored as markdown alongside this module.

Keeps prompt *content* in `.md` files (easy to edit/diff/version) and this thin
*code* layer just reads and fills runtime placeholders (e.g. today's date).

Two files, on purpose. `system.md` is the contract: who the agent is, what the
tools do, what it may not do. `program.md` is the research strategy — which
experiments to try in what order, when to keep a direction, when to stop. That
one is meant to be rewritten often, by a human tuning how the search behaves,
without touching any Python.
"""

from __future__ import annotations

from datetime import date
from importlib.resources import files


def _read(name: str) -> str:
    return files(__package__).joinpath(name).read_text(encoding="utf-8")


def get_program() -> str:
    """The research strategy the agent follows between experiments."""
    return _read("program.md").strip()


def get_system_prompt(*, leaderboard: str = "", program: str | None = None) -> str:
    """The agent's system prompt, with runtime placeholders filled."""
    template = _read("system.md")
    return (
        template.replace("{today}", date.today().isoformat())
        .replace(
            "{leaderboard}",
            leaderboard.strip() or "No training runs on this conversation yet.",
        )
        .replace("{program}", get_program() if program is None else program.strip())
    )
