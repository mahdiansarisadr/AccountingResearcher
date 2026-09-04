"""One frozen split per dataset, shared by every version.

Comparing two versions only means something if they were scored on the same
rows. The split is derived from the data itself — a content fingerprint plus a
seed — so it comes out identical across processes, restarts and machines
without anyone having to store it.

Three parts, not two. The agent optimises against ``valid``; ``test`` is held
back and never shown to it. An autonomous loop picks the maximum over hundreds
of attempts, so the winner is partly selected on the noise in whatever set it
was selected on, and its score there is optimistic. Only a set that took no
part in the selection can say what the model is actually worth.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

TEST_FRACTION = 0.2
VALID_FRACTION = 0.2
DEFAULT_SEED = 42

# Below this, a three-way split leaves too few rows to say anything. The test
# set is dropped rather than reported as if it meant something.
_MIN_ROWS_FOR_TEST = 40


@dataclass(frozen=True)
class Split:
    """Positional row indices for one dataset, fixed for the life of a thread."""

    fingerprint: str
    seed: int
    train: np.ndarray
    valid: np.ndarray
    test: np.ndarray

    @property
    def has_test(self) -> bool:
        return len(self.test) > 0

    def as_params(self) -> dict[str, str]:
        """What to log so a run says which rows it was scored on."""
        return {
            "data_fingerprint": self.fingerprint[:16],
            "split_seed": str(self.seed),
            "n_train": str(len(self.train)),
            "n_valid": str(len(self.valid)),
            "n_test": str(len(self.test)),
        }


def fingerprint(frame: pd.DataFrame) -> str:
    """Content address for a table.

    Two uploads with the same name are not the same dataset, and runs scored on
    different data are not comparable. Hashing the contents is what notices.
    """
    hashed = pd.util.hash_pandas_object(frame, index=True).values
    digest = hashlib.sha256(hashed.tobytes())
    digest.update(",".join(map(str, frame.columns)).encode("utf-8"))
    return digest.hexdigest()


def _stratify_labels(frame: pd.DataFrame, target: str) -> pd.Series | None:
    """Class labels to stratify on, or None when the target is continuous."""
    column = frame[target]
    if pd.api.types.is_numeric_dtype(column) and column.nunique(dropna=True) > 10:
        return None
    labels = column.astype(str)
    # A class that appears once cannot be split across three parts.
    if labels.value_counts().min() < 3:
        return None
    return labels


def make_split(
    frame: pd.DataFrame,
    target: str,
    *,
    seed: int = DEFAULT_SEED,
) -> Split:
    """Split rows into train / valid / test, the same way every time."""
    indices = np.arange(len(frame))
    labels = _stratify_labels(frame, target)

    if len(frame) < _MIN_ROWS_FOR_TEST:
        train, valid = _cut(indices, VALID_FRACTION, seed, labels)
        return Split(
            fingerprint=fingerprint(frame),
            seed=seed,
            train=train,
            valid=valid,
            test=np.array([], dtype=int),
        )

    rest, test = _cut(indices, TEST_FRACTION, seed, labels)
    # The valid fraction is of the whole table, so scale it to what is left.
    remaining = VALID_FRACTION / (1.0 - TEST_FRACTION)
    rest_labels = labels.iloc[rest] if labels is not None else None
    train, valid = _cut(rest, remaining, seed, rest_labels)
    return Split(
        fingerprint=fingerprint(frame),
        seed=seed,
        train=train,
        valid=valid,
        test=test,
    )


def _cut(
    indices: np.ndarray,
    fraction: float,
    seed: int,
    labels: pd.Series | None,
) -> tuple[np.ndarray, np.ndarray]:
    """One stratified cut, falling back to a plain one when strata will not hold."""
    try:
        left, right = train_test_split(
            indices,
            test_size=fraction,
            random_state=seed,
            stratify=None if labels is None else np.asarray(labels),
        )
    except ValueError:
        left, right = train_test_split(indices, test_size=fraction, random_state=seed)
    return np.sort(left), np.sort(right)
