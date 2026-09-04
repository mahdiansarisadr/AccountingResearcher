"""Recipes: the versioned training source behind every run.

A run *executes* a recipe version, it does not define one. Versions are
numbered per conversation, carry a ``parent`` so the history is a tree rather
than a line, and are content-addressed so resubmitting identical source lands
on the version it already has. Many runs to one version is the point: running
v4 again measures noise, it does not look like a new idea.

The store is the per-user MLflow the runs already live in — version metadata on
run tags, source as a run artifact — so there is one source of truth about what
was tried.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import mlflow
from mlflow.tracking import MlflowClient

from .workspace import tracking_uri

VERSION_TAG = "mleng.recipe_version"
SHA_TAG = "mleng.recipe_sha"
PARENT_TAG = "mleng.recipe_parent"
KIND_TAG = "mleng.recipe_kind"
REUSED_TAG = "mleng.recipe_reused"

CODE = "code"
DEFAULT = "default"

# Params that reproduce a built-in run. A code recipe needs none of these: its
# artifact is the whole story.
SPEC_PARAMS = ("task", "model", "target", "estimator_params")

# One scan has to see every run or the next version number could collide with
# one already handed out. Threads do not get near this.
_SCAN_LIMIT = 1000


class RecipeError(ValueError):
    """A recipe reference the caller got wrong: unknown version, unknown parent."""


@dataclass(frozen=True)
class RecipeVersion:
    """One version of the training source, plus where to read it back from."""

    version: int
    kind: str
    sha: str
    parent: int | None
    spec: dict[str, str] = field(default_factory=dict)
    source: str | None = None
    run_ids: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"v{self.version}"

    def describe(self) -> str:
        """What this version is, in one block, for the agent to read."""
        if self.kind == CODE:
            return self.source or "(source artifact is missing for this version)"
        return describe_default(self.spec)


@dataclass(frozen=True)
class Allocation:
    """The version a train call should run under."""

    version: int
    kind: str
    sha: str
    parent: int | None
    reused: bool


def normalise_source(source: str) -> str:
    """Cosmetic whitespace must not fork a version."""
    lines = [line.rstrip() for line in (source or "").strip().splitlines()]
    return "\n".join(lines) + "\n" if lines else ""


def default_spec(
    *, task: str, model: str, target: str, estimator_params: str = ""
) -> dict[str, str]:
    return {
        "task": task,
        "model": model,
        "target": target,
        "estimator_params": estimator_params,
    }


def describe_default(spec: dict[str, str]) -> str:
    """The built-in pipeline written down, so a default run is a readable recipe."""
    model = spec.get("model") or "the default estimator"
    lines = [
        "Built-in pipeline (no agent code).",
        "Features: every column except the target, used as-is — median impute and "
        "standard scale for numeric columns, most-frequent impute and one-hot "
        "encode for the rest. No engineered features.",
        f"Estimator: {model}.",
    ]
    params = spec.get("estimator_params")
    if params:
        lines.append(f"Hyperparameters: {params}")
    task = spec.get("task")
    if task:
        lines.append(f"Task: {task}.")
    return "\n".join(lines)


def source_path(version: int) -> str:
    return f"recipe/v{version}.py"


def recipe_sha(kind: str, *, source: str | None = None, spec: dict[str, str] | None = None) -> str:
    """Content address. Identical work gets an identical hash, and so one version.

    A recipe is a script *for a target*: the same code predicting a different
    column is a different idea, not a re-run of this one. The split seed is
    deliberately not part of the address — varying it is how a version measures
    its own noise.
    """
    spec = spec or {}
    if kind == CODE:
        payload = "code\n" + (spec.get("target") or "") + "\n" + normalise_source(source or "")
    else:
        payload = "default\n" + json.dumps(spec, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _client(user_id: str, data_dir: Path | None) -> MlflowClient:
    return MlflowClient(tracking_uri=tracking_uri(user_id, data_dir=data_dir))


def catalogue(
    user_id: str, thread_id: str, *, data_dir: Path | None = None
) -> dict[int, RecipeVersion]:
    """Every version on this conversation, keyed by version number.

    Source is not fetched here — the tree view needs the shape, not the code.
    Use :func:`load` for that.
    """
    client = _client(user_id, data_dir)
    experiment = client.get_experiment_by_name(str(thread_id))
    if experiment is None:
        return {}
    runs = client.search_runs(
        [experiment.experiment_id],
        order_by=["start_time DESC"],
        max_results=_SCAN_LIMIT,
    )

    found: dict[int, RecipeVersion] = {}
    runs_by_version: dict[int, list[str]] = {}
    for run in runs:
        tags = dict(run.data.tags or {})
        version = _as_int(tags.get(VERSION_TAG))
        if version is None:
            continue
        runs_by_version.setdefault(version, []).append(run.info.run_id)
        if version in found:
            continue
        params = dict(run.data.params or {})
        found[version] = RecipeVersion(
            version=version,
            kind=(tags.get(KIND_TAG) or CODE).strip() or CODE,
            sha=(tags.get(SHA_TAG) or "").strip(),
            parent=_as_int(tags.get(PARENT_TAG)),
            spec={key: params[key] for key in SPEC_PARAMS if key in params},
        )
    return {
        version: RecipeVersion(
            version=recipe.version,
            kind=recipe.kind,
            sha=recipe.sha,
            parent=recipe.parent,
            spec=recipe.spec,
            run_ids=tuple(runs_by_version.get(version, ())),
        )
        for version, recipe in sorted(found.items())
    }


def load(
    user_id: str, thread_id: str, version: int, *, data_dir: Path | None = None
) -> RecipeVersion:
    """One version with its source read back. Raises when the version is unknown."""
    known = catalogue(user_id, thread_id, data_dir=data_dir)
    recipe = known.get(int(version))
    if recipe is None:
        raise RecipeError(_unknown(version, known))
    if recipe.kind != CODE:
        return recipe
    source = _read_source(user_id, recipe, data_dir=data_dir)
    return RecipeVersion(
        version=recipe.version,
        kind=recipe.kind,
        sha=recipe.sha,
        parent=recipe.parent,
        spec=recipe.spec,
        source=source,
        run_ids=recipe.run_ids,
    )


def allocate(
    user_id: str,
    thread_id: str,
    *,
    kind: str,
    source: str | None = None,
    spec: dict[str, str] | None = None,
    parent: int | None = None,
    data_dir: Path | None = None,
) -> Allocation:
    """The version this training source runs under: the one it has, or the next free one.

    Identical content is never given a second number, so a re-run is visibly a
    re-run. A version's parent is set once, when it is first created.
    """
    known = catalogue(user_id, thread_id, data_dir=data_dir)
    if parent is not None and int(parent) not in known:
        raise RecipeError(_unknown(parent, known, noun="parent version"))

    sha = recipe_sha(kind, source=source, spec=spec)
    for recipe in known.values():
        if recipe.sha == sha:
            return Allocation(
                version=recipe.version,
                kind=recipe.kind,
                sha=sha,
                parent=recipe.parent,
                reused=True,
            )
    return Allocation(
        version=(max(known) + 1) if known else 1,
        kind=kind,
        sha=sha,
        parent=int(parent) if parent is not None else None,
        reused=False,
    )


def stamp(allocation: Allocation, *, source: str | None = None) -> None:
    """Tag the active MLflow run with its version and attach the source.

    The artifact goes on every run, not just the first, so a run stays readable
    on its own.
    """
    mlflow.set_tags(
        {
            VERSION_TAG: str(allocation.version),
            KIND_TAG: allocation.kind,
            SHA_TAG: allocation.sha,
            PARENT_TAG: "" if allocation.parent is None else str(allocation.parent),
            REUSED_TAG: "true" if allocation.reused else "false",
        }
    )
    if allocation.kind == CODE and source:
        mlflow.log_text(normalise_source(source), source_path(allocation.version))


def _read_source(
    user_id: str, recipe: RecipeVersion, *, data_dir: Path | None
) -> str | None:
    client = _client(user_id, data_dir)
    path = source_path(recipe.version)
    for run_id in recipe.run_ids:
        try:
            run = client.get_run(run_id)
            return mlflow.artifacts.load_text(f"{run.info.artifact_uri}/{path}")
        except Exception:  # noqa: BLE001 - an older run may predate the artifact
            continue
    return None


def _as_int(value: str | None) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _unknown(version: int, known: dict[int, RecipeVersion], noun: str = "version") -> str:
    if not known:
        return f"no recipe {noun} {version}: nothing has been trained on this conversation yet"
    available = ", ".join(f"v{number}" for number in sorted(known))
    return f"no recipe {noun} v{version} on this conversation. Known versions: {available}"
