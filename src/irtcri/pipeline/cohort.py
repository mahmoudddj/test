"""Cohort-flow accounting (CONSORT/STROBE-style) for every generated dataset."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class CohortFlow:
    steps: list[dict] = field(default_factory=list)

    def add(self, stage: str, patients: int | None = None, episodes: int | None = None, images: int | None = None,
            note: str = "") -> None:
        self.steps.append({"stage": stage, "patients": patients, "episodes": episodes, "images": images,
                           "note": note})

    def exclude(self, reason: str, patients: int = 0, episodes: int = 0, images: int = 0) -> None:
        self.steps.append({"stage": f"  excluded: {reason}", "patients": patients, "episodes": episodes,
                           "images": images, "note": "exclusion"})

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self.steps)

    def to_markdown(self) -> str:
        lines = ["| stage | patients | episodes | images |", "|---|---:|---:|---:|"]
        for s in self.steps:
            f = lambda v: "" if v is None else str(v)  # noqa: E731
            lines.append(f"| {s['stage']} | {f(s['patients'])} | {f(s['episodes'])} | {f(s['images'])} |")
        return "\n".join(lines)


def screening_counts(screening: pd.DataFrame, patients: pd.DataFrame, flow: CohortFlow) -> None:
    if len(screening):
        flow.add("screened", patients=int(len(screening)))
        eligible = screening[screening["eligible"].fillna(False).astype(bool)]
        flow.add("eligible", patients=int(len(eligible)))
        not_el = screening[~screening["eligible"].fillna(False).astype(bool)]
        for reason, n in not_el["exclusion_reason"].fillna("not recorded").value_counts().items():
            flow.exclude(f"not eligible - {reason}", patients=int(n))
        for reason, n in screening[screening["eligible"].fillna(False).astype(bool) &
                                   (screening["outcome"] != "enrolled")]["outcome"].value_counts().items():
            flow.exclude(f"eligible, not enrolled - {reason}", patients=int(n))
    flow.add("enrolled", patients=int(len(patients)))
