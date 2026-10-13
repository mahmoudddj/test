"""Dependency-free Markdown table rendering."""

from __future__ import annotations

import pandas as pd


def to_markdown(df: pd.DataFrame, floatfmt: str = ".3g") -> str:
    def f(v):
        if isinstance(v, float):
            return "" if pd.isna(v) else format(v, floatfmt)
        return "" if v is None else str(v)
    cols = list(df.columns)
    lines = ["| " + " | ".join(map(str, cols)) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(f(v) for v in row) + " |")
    return "\n".join(lines)
