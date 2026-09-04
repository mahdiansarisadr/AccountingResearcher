"""Tool: report_progress — the whole search, first run to last.

Called once the iterating is done, before the closing summary is written. The
agent has been reading a version *tree* all session, which says what exists but
not what happened in what order or whether the total movement outran the noise.
This is that, and it is what a summary should be built on rather than an
impression of how the session felt.
"""

from __future__ import annotations

from langchain.tools import tool

from ...core.progress import format_progress, summarise_progress
from ...core.workspace import current


def report_progress_impl() -> str:
    ctx = current()
    return format_progress(
        summarise_progress(ctx.user_id, ctx.thread_id, data_dir=ctx.data_dir)
    )


@tool
def report_progress() -> str:
    """Show how performance moved across the whole search, in the order it happened.

    Call this when you have stopped iterating and before you write your final
    answer. It returns every run oldest first, the best score at each point,
    which runs set a new best, the total gain from the first score to the last,
    and the noise floor measured by re-running a version unchanged.

    Base your closing summary on what this returns. In particular, do not
    describe the search as an improvement if the total gain is not larger than
    the noise floor — say plainly that it did not find one.
    """
    try:
        return report_progress_impl()
    except Exception as exc:  # noqa: BLE001 - surfaced to the model
        return f"ERROR: {exc}"
