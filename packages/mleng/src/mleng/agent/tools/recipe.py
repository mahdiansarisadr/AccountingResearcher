"""Tool: get_recipe — read back the exact source a recipe version ran.

The prompt's version tree says what was tried and what it scored. It cannot
carry the scripts. This is how the agent edits earlier work instead of
reconstructing it from its own summary of it.
"""

from __future__ import annotations

from langchain.tools import tool

from ...core import recipes
from ...core.experiments import list_experiment_runs, summarise_versions
from ...core.workspace import current

_SOURCE_CHARS = 20_000


def get_recipe_impl(version: int) -> str:
    ctx = current()
    try:
        found = recipes.load(ctx.user_id, ctx.thread_id, int(version), data_dir=ctx.data_dir)
    except (recipes.RecipeError, TypeError, ValueError) as exc:
        return f"ERROR: {exc}"

    summary = summarise_versions(
        list_experiment_runs(ctx.user_id, ctx.thread_id, data_dir=ctx.data_dir)
    ).get(found.version)

    header = [f"Recipe v{found.version} [{found.kind}]"]
    if found.parent is not None:
        header.append(f"derived from v{found.parent}")
    if summary is not None:
        header.append(f"{summary.runs} run" + ("s" if summary.runs != 1 else ""))
        if summary.best_value is not None:
            header.append(f"best {summary.best_metric}={summary.best_value:.4g}")
        if summary.last_error:
            header.append(f"last failure: {summary.last_error}")

    body = found.describe()
    if len(body) > _SOURCE_CHARS:
        body = body[:_SOURCE_CHARS] + "\n# ... truncated ..."
    lead = (
        "Edit this and pass the whole result as code= with "
        f"parent_version={found.version}, or run it unchanged with "
        f"recipe_version={found.version}."
    )
    return "\n".join([" — ".join(header), lead, "", body])


@tool
def get_recipe(version: int) -> str:
    """Read the training source of a recipe version on this conversation.

    Use this before changing earlier work: fetch the version you want to build
    on, edit that source, and submit the whole thing as ``train_model(code=...,
    parent_version=<that version>)``. Do not rewrite a previous approach from
    memory when you can read it.

    Args:
        version: The version number shown in the tree, without the leading "v".
    """
    try:
        return get_recipe_impl(version)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model
        return f"ERROR: {exc}"
