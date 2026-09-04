"""Recipe versioning: one number per idea, many runs per number."""

from __future__ import annotations

import json
from pathlib import Path

from mleng.agent.tools.recipe import get_recipe_impl
from mleng.agent.tools.train import train_model_impl
from mleng.core import recipes
from mleng.core.experiments import (
    format_leaderboard,
    list_experiment_runs,
    summarise_versions,
)
from mleng.core.workspace import reset_run_context, save_upload, set_run_context

REGRESSION_CSV = """x,z,y
1,2,3.0
2,1,3.5
3,4,8.0
4,3,8.5
5,6,13.0
6,5,13.5
7,8,18.0
8,7,18.5
9,10,23.0
10,9,23.5
11,12,28.0
12,11,28.5
"""

RIDGE_CODE = """
from sklearn.linear_model import Ridge

model = Ridge()
model.fit(features(train), train[target])
"""

LASSO_CODE = """
from sklearn.linear_model import Lasso

model = Lasso(alpha=0.1)
model.fit(features(train), train[target])
"""


class Workspace:
    """One user, one thread, one uploaded table."""

    def __init__(self, tmp_path: Path, user: str = "user-a", thread: str = "thread-1") -> None:
        self.tmp_path = tmp_path
        self.user = user
        self.thread = thread
        save_upload(user, thread, "data.csv", REGRESSION_CSV.encode(), data_dir=tmp_path)

    def train(self, target: str = "y", **kwargs):
        token = set_run_context(self.user, self.thread, data_dir=self.tmp_path)
        try:
            raw = train_model_impl(target, filename="data.csv", **kwargs)
        finally:
            reset_run_context(token)
        return raw if raw.startswith("ERROR:") else json.loads(raw)

    def recipe(self, version: int) -> str:
        token = set_run_context(self.user, self.thread, data_dir=self.tmp_path)
        try:
            return get_recipe_impl(version)
        finally:
            reset_run_context(token)

    def versions(self):
        rows = list_experiment_runs(self.user, self.thread, data_dir=self.tmp_path)
        return summarise_versions(rows)

    def tree(self) -> str:
        return format_leaderboard(self.user, self.thread, data_dir=self.tmp_path)


def test_first_run_creates_version_one(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    result = space.train(model="ridge", task="regression")

    assert result["recipe_version"] == 1
    assert result["recipe_parent"] is None
    assert result["reused_recipe"] is False


def test_identical_default_reuses_its_version(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    first = space.train(model="ridge", task="regression")
    second = space.train(model="ridge", task="regression")

    assert second["recipe_version"] == first["recipe_version"]
    assert second["reused_recipe"] is True
    assert space.versions()[1].runs == 2


def test_a_different_estimator_is_a_new_version(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    space.train(model="ridge", task="regression")
    other = space.train(model="random_forest", task="regression")

    assert other["recipe_version"] == 2
    assert other["reused_recipe"] is False


def test_code_records_the_version_it_was_derived_from(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    base = space.train(model="ridge", task="regression")
    derived = space.train(
        code=RIDGE_CODE, parent_version=base["recipe_version"], hypothesis="hand written ridge"
    )

    assert derived["recipe_version"] == 2
    assert derived["recipe_parent"] == 1
    assert space.versions()[2].parent == 1


def test_identical_code_reuses_its_version(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    first = space.train(code=RIDGE_CODE)
    # Whitespace is cosmetic; it must not fork a version.
    second = space.train(code="\n\n" + RIDGE_CODE.replace("\n", "  \n") + "\n")

    assert second["recipe_version"] == first["recipe_version"]
    assert second["reused_recipe"] is True


def test_changed_code_forks_a_new_version(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    first = space.train(code=RIDGE_CODE)
    second = space.train(code=LASSO_CODE, parent_version=first["recipe_version"])

    assert second["recipe_version"] == 2
    assert second["recipe_parent"] == 1


def test_same_code_on_another_target_is_a_different_recipe(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    first = space.train(target="y", code=RIDGE_CODE)
    second = space.train(target="z", code=RIDGE_CODE)

    assert second["recipe_version"] != first["recipe_version"]


def test_rerunning_a_code_version_replays_its_source(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    first = space.train(code=RIDGE_CODE, hypothesis="hand written ridge")
    again = space.train(recipe_version=first["recipe_version"])

    assert again["recipe_version"] == first["recipe_version"]
    assert again["reused_recipe"] is True
    assert again["metrics"]["r2"] == first["metrics"]["r2"]
    assert space.versions()[first["recipe_version"]].runs == 2


def test_rerunning_a_default_version_replays_its_estimator(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    first = space.train(model="ridge", task="regression")
    again = space.train(recipe_version=first["recipe_version"])

    assert again["recipe_version"] == first["recipe_version"]
    assert again["model"] == "ridge"
    assert again["reused_recipe"] is True


def test_a_new_seed_is_another_run_of_the_same_version(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    first = space.train(code=RIDGE_CODE)
    second = space.train(recipe_version=first["recipe_version"], split_seed=7)

    assert second["recipe_version"] == first["recipe_version"]
    assert second["split_seed"] == 7
    summary = space.versions()[first["recipe_version"]]
    assert summary.runs == 2
    assert summary.spread is not None


def test_unknown_version_is_a_helpful_error(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    space.train(model="ridge", task="regression")

    assert "no recipe version v9" in space.train(recipe_version=9)
    assert "no recipe parent version v9" in space.train(code=RIDGE_CODE, parent_version=9)


def test_code_and_recipe_version_together_is_refused(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    space.train(code=RIDGE_CODE)
    refusal = space.train(code=LASSO_CODE, recipe_version=1)

    assert refusal.startswith("ERROR:")
    assert "parent_version=1" in refusal


def test_a_failed_version_keeps_its_reason(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    broken = space.train(code="raise ValueError('bad transform')")

    assert broken.startswith("ERROR:")
    summary = space.versions()[1]
    assert summary.failed == 1
    assert summary.last_error is not None
    assert "bad transform" in summary.last_error
    assert "bad transform" in space.tree()


def test_get_recipe_returns_the_source_to_edit(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    space.train(code=RIDGE_CODE, hypothesis="hand written ridge")
    text = space.recipe(1)

    assert "Recipe v1 [code]" in text
    assert "parent_version=1" in text
    assert "Ridge()" in text


def test_get_recipe_describes_a_default_version(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    space.train(model="ridge", task="regression")
    text = space.recipe(1)

    assert "Recipe v1 [default]" in text
    assert "Built-in pipeline" in text
    assert "Hyperparameters" in text


def test_get_recipe_rejects_an_unknown_version(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    assert space.recipe(3).startswith("ERROR:")


def test_tree_nests_versions_under_their_parent(tmp_path: Path) -> None:
    space = Workspace(tmp_path)
    space.train(model="ridge", task="regression")
    space.train(code=RIDGE_CODE, parent_version=1, hypothesis="hand written ridge")
    space.train(code=LASSO_CODE, parent_version=2, hypothesis="lasso instead")

    tree = space.tree()
    lines = [line for line in tree.splitlines() if line.strip().startswith("v")]

    assert lines[0].startswith("v1 ")
    assert lines[1].startswith("  v2 ")
    assert lines[2].startswith("    v3 ")
    assert "Best holdout so far" in tree
    assert "lasso instead" in tree


def test_versions_are_scoped_to_one_conversation(tmp_path: Path) -> None:
    first = Workspace(tmp_path, thread="thread-1")
    second = Workspace(tmp_path, thread="thread-2")
    first.train(code=RIDGE_CODE)
    first.train(code=LASSO_CODE, parent_version=1)
    fresh = second.train(code=LASSO_CODE)

    assert fresh["recipe_version"] == 1


def test_sha_ignores_the_split_seed(tmp_path: Path) -> None:
    spec = {"target": "y"}
    assert recipes.recipe_sha(recipes.CODE, source=RIDGE_CODE, spec=spec) == recipes.recipe_sha(
        recipes.CODE, source=RIDGE_CODE + "\n", spec=spec
    )
