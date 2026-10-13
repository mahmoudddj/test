"""Data-dictionary loader and value validation.

The YAML dictionary (configs/data_dictionary.yaml) is the single source of
truth; this module turns it into typed objects and implements:

* unit conversion to the canonical unit,
* hard-range (impossible value -> reject) and plausible-range (flag) checks,
* categorical/boolean checks,
* cross-field rules evaluated on values sharing a timestamp,
* availability-time computation used for future-leakage control.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Literal

import yaml
from pydantic import BaseModel, Field

from irtcri.core.config import get_settings

Entity = Literal["observation", "laboratory", "patient", "assessment", "image"]


class UnitConversion(BaseModel):
    scale: float = 1.0
    offset: float = 0.0


class Availability(BaseModel):
    basis: Literal["event_time", "source_time"] = "event_time"
    lag_minutes: int = 0


class Variable(BaseModel):
    name: str
    label: str
    entity: Entity
    category: str
    type: Literal["float", "int", "bool", "category", "string"]
    unit: str | None = None
    unit_alternatives: dict[str, UnitConversion] = Field(default_factory=dict)
    hard_range: tuple[float, float] | None = None
    plausible_range: tuple[float, float] | None = None
    allowed_values: list[str] | None = None
    source: str = ""
    time_reference: str = ""
    availability: Availability = Field(default_factory=Availability)
    expected_interval_hours: float | None = None
    required: bool = False
    protocol_status: Literal["core", "optional"] = "core"
    aliases: list[str] = Field(default_factory=list)
    validation: list[str] = Field(default_factory=list)


class DataDictionary(BaseModel):
    version: str
    missing_codes: dict[str, str]
    missing_default: str = ""
    variables: list[Variable]

    def model_post_init(self, __context: Any) -> None:
        self._by_name = {v.name: v for v in self.variables}
        self._alias = {}
        for v in self.variables:
            self._alias[v.name.lower()] = v.name
            for a in v.aliases:
                self._alias[a.lower()] = v.name

    def __contains__(self, name: str) -> bool:
        return name in self._by_name

    def get(self, name: str) -> Variable:
        try:
            return self._by_name[name]
        except KeyError:
            raise KeyError(f"Unknown variable '{name}' (not in data dictionary v{self.version})") from None

    def resolve(self, name_or_alias: str) -> str | None:
        return self._alias.get(name_or_alias.strip().lower())

    def by_entity(self, entity: Entity) -> list[Variable]:
        return [v for v in self.variables if v.entity == entity]


@lru_cache
def load_dictionary(path: str | None = None) -> DataDictionary:
    p = Path(path) if path else get_settings().config_path("data_dictionary.yaml")
    with open(p) as f:
        return DataDictionary.model_validate(yaml.safe_load(f))


# ------------------------------------------------------------------ validation
@dataclass
class ValidationResult:
    variable: str
    value: Any                      # canonical value (converted)
    unit: str | None
    errors: list[str] = field(default_factory=list)     # -> reject
    flags: list[str] = field(default_factory=list)      # -> store + flag

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_value(var: Variable, value: Any, unit: str | None = None,
                   missing_reason: str | None = None,
                   dictionary: DataDictionary | None = None) -> ValidationResult:
    res = ValidationResult(var.name, None, var.unit)
    if value is None or (isinstance(value, float) and value != value):
        if missing_reason is not None:
            codes = (dictionary or load_dictionary()).missing_codes
            if missing_reason not in codes:
                res.errors.append(f"unknown missing_reason '{missing_reason}' (allowed: {sorted(codes)})")
        return res

    if var.type in ("float", "int"):
        try:
            num = float(value)
        except (TypeError, ValueError):
            res.errors.append(f"value '{value}' is not numeric")
            return res
        if unit and var.unit and unit != var.unit:
            conv = var.unit_alternatives.get(unit)
            if conv is None:
                res.errors.append(
                    f"unit '{unit}' not accepted for {var.name}; canonical '{var.unit}', "
                    f"alternatives {sorted(var.unit_alternatives)}")
                return res
            num = num * conv.scale + conv.offset
            res.flags.append(f"converted_from_{unit}")
        if var.type == "int":
            if abs(num - round(num)) > 1e-9:
                res.errors.append(f"{var.name} must be an integer (got {num})")
                return res
            num = int(round(num))
        if var.hard_range and not (var.hard_range[0] <= num <= var.hard_range[1]):
            res.errors.append(f"impossible value {num:g} {var.unit or ''} outside hard range {list(var.hard_range)}")
        elif var.plausible_range and not (var.plausible_range[0] <= num <= var.plausible_range[1]):
            res.flags.append(f"implausible:{num:g} outside {list(var.plausible_range)}")
        res.value = num
    elif var.type == "bool":
        if isinstance(value, bool):
            res.value = value
        elif str(value).strip().lower() in {"1", "true", "yes", "y"}:
            res.value = True
        elif str(value).strip().lower() in {"0", "false", "no", "n"}:
            res.value = False
        else:
            res.errors.append(f"value '{value}' is not boolean")
    elif var.type == "category":
        sval = str(value).strip()
        if var.allowed_values and sval not in var.allowed_values:
            res.errors.append(f"'{sval}' not in allowed values {var.allowed_values}")
        res.value = sval
    else:
        res.value = str(value)
    return res


def availability_time(var: Variable, event_time: datetime, source_time: datetime | None) -> datetime:
    """When could this value first have been known? (Leakage control.)

    Uses the documented ``source_time`` (e.g. lab report time) when the
    dictionary says availability is source-based and the source time is known;
    otherwise ``event_time + lag``. The conservative lag protects against
    using e.g. a lab drawn at 08:00 but reported at 12:00 for a 09:00 prediction.
    """
    lag = timedelta(minutes=var.availability.lag_minutes)
    if var.availability.basis == "source_time" and source_time is not None:
        return max(source_time, event_time)
    return event_time + lag


# ------------------------------------------------------------ cross-field rules
CrossRule = Callable[[dict[str, float]], str | None]


def _sbp_gt_dbp(v: dict[str, float]) -> str | None:
    if "sbp" in v and "dbp" in v and not v["sbp"] > v["dbp"]:
        return f"sbp ({v['sbp']}) must exceed dbp ({v['dbp']})"
    return None


def _map_between(v: dict[str, float]) -> str | None:
    if {"sbp", "dbp", "map"} <= v.keys() and not (v["dbp"] <= v["map"] <= v["sbp"]):
        return f"map ({v['map']}) must lie between dbp ({v['dbp']}) and sbp ({v['sbp']})"
    return None


CROSS_FIELD_RULES: dict[str, CrossRule] = {
    "sbp_gt_dbp": _sbp_gt_dbp,
    "map_between_dbp_sbp": _map_between,
}


def check_cross_field(values_at_time: dict[str, float]) -> list[str]:
    out = []
    for name, rule in CROSS_FIELD_RULES.items():
        msg = rule(values_at_time)
        if msg:
            out.append(f"{name}: {msg}")
    return out


def dictionary_markdown(d: DataDictionary | None = None) -> str:
    d = d or load_dictionary()
    lines = [f"# IRT-in-CRI Data Dictionary (v{d.version})", "",
             "_Generated from `configs/data_dictionary.yaml` - do not edit by hand._", "",
             "## Missing-value codes", ""]
    lines += [f"* `{k}` - {v}" for k, v in d.missing_codes.items()]
    lines += ["", d.missing_default, ""]
    for entity in ("patient", "observation", "laboratory", "assessment", "image"):
        vs = d.by_entity(entity)  # type: ignore[arg-type]
        if not vs:
            continue
        lines += [f"## Entity: `{entity}`", "",
                  "| name | label | type | unit (alternatives) | hard range | plausible range | "
                  "availability | interval h | required | source | time reference |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for v in vs:
            alts = ", ".join(v.unit_alternatives) if v.unit_alternatives else ""
            unit = f"{v.unit or ''}" + (f" ({alts})" if alts else "")
            if v.allowed_values:
                unit = "{" + ", ".join(v.allowed_values) + "}"
            avail = f"{v.availability.basis}+{v.availability.lag_minutes}min"
            lines.append(
                f"| `{v.name}` | {v.label} | {v.type} | {unit} | {list(v.hard_range) if v.hard_range else ''} | "
                f"{list(v.plausible_range) if v.plausible_range else ''} | {avail} | "
                f"{v.expected_interval_hours or ''} | {'yes' if v.required else ''} | {v.source} | "
                f"{v.time_reference} |")
        lines.append("")
    return "\n".join(lines)


CSV_COLUMNS = ["name", "label", "entity", "category", "type", "unit", "unit_alternatives", "hard_min", "hard_max",
               "plausible_min", "plausible_max", "allowed_values", "missing_representation", "source",
               "time_reference", "availability_basis", "availability_lag_minutes", "expected_interval_hours",
               "required", "protocol_status", "aliases", "validation_rules"]


def dictionary_csv(d: DataDictionary | None = None) -> str:
    """Flat CSV view of the dictionary (docs/DATA_DICTIONARY.csv). The YAML file stays the source of truth."""
    import csv
    import io
    d = d or load_dictionary()
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    w.writeheader()
    for v in d.variables:
        w.writerow({
            "name": v.name, "label": v.label, "entity": v.entity, "category": v.category, "type": v.type,
            "unit": v.unit or "",
            "unit_alternatives": "; ".join(f"{u} (x{c.scale:g}{c.offset:+g})" if c.offset else f"{u} (x{c.scale:g})"
                                           for u, c in v.unit_alternatives.items()),
            "hard_min": v.hard_range[0] if v.hard_range else "", "hard_max": v.hard_range[1] if v.hard_range else "",
            "plausible_min": v.plausible_range[0] if v.plausible_range else "",
            "plausible_max": v.plausible_range[1] if v.plausible_range else "",
            "allowed_values": "|".join(v.allowed_values or []),
            "missing_representation": "NULL + missing_reason in {" + ",".join(d.missing_codes) + "}",
            "source": v.source, "time_reference": v.time_reference,
            "availability_basis": v.availability.basis, "availability_lag_minutes": v.availability.lag_minutes,
            "expected_interval_hours": v.expected_interval_hours if v.expected_interval_hours is not None else "",
            "required": str(v.required).lower(), "protocol_status": v.protocol_status,
            "aliases": "|".join(v.aliases),
            "validation_rules": "; ".join(["hard range -> reject", "plausible range -> flag + auto query"]
                                          * bool(v.hard_range) + v.validation),
        })
    return buf.getvalue()
