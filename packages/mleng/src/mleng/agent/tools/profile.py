"""Tool: profile_dataset — column stats for the thread's uploaded table."""

from __future__ import annotations

import json

import pandas as pd
from langchain.tools import tool

from ...core.workspace import current, list_uploads, resolve_upload


def _load_table(path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix in {".parquet", ".pq"}:
        return pd.read_parquet(path)
    raise ValueError(f"unsupported file type: {suffix}")


def profile_dataset_impl(filename: str | None = None) -> str:
    ctx = current()
    files = list_uploads(ctx.user_id, ctx.thread_id, data_dir=ctx.data_dir)
    if not files:
        return "ERROR: no dataset uploaded on this conversation yet."

    path = resolve_upload(
        ctx.user_id, ctx.thread_id, filename, data_dir=ctx.data_dir
    )
    frame = _load_table(path)
    columns = []
    for name in frame.columns:
        series = frame[name]
        missing = int(series.isna().sum())
        entry = {
            "name": str(name),
            "dtype": str(series.dtype),
            "missing": missing,
            "missing_pct": round(missing / max(len(frame), 1), 4),
            "n_unique": int(series.nunique(dropna=True)),
        }
        if pd.api.types.is_numeric_dtype(series):
            entry["min"] = _json_num(series.min())
            entry["max"] = _json_num(series.max())
            entry["mean"] = _json_num(series.mean())
        else:
            top = series.dropna().astype(str).value_counts().head(5)
            entry["top_values"] = {str(k): int(v) for k, v in top.items()}
        columns.append(entry)

    payload = {
        "file": path.name,
        "rows": int(len(frame)),
        "n_columns": int(len(frame.columns)),
        "available_files": [item.name for item in files],
        "columns": columns,
        "sample": json.loads(frame.head(5).to_json(orient="records")),
    }
    return json.dumps(payload, default=str)


def _json_num(value):
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


@tool
def profile_dataset(filename: str | None = None) -> str:
    """Inspect the dataset uploaded to this conversation.

    Returns column names, types, missingness, a few sample rows, and the list
    of files on this thread. Pass filename to pick one; omit it to use the
    most recently uploaded file. Call this before training if you do not
    already know the target column name.
    """
    try:
        return profile_dataset_impl(filename)
    except Exception as exc:  # noqa: BLE001 - surfaced to the model
        return f"ERROR: {exc}"
