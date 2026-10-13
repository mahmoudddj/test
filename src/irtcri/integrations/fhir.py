"""Optional FHIR R4 adapter (NOT required for the MVP).

Maps FHIR ``Observation`` resources (as JSON dicts, e.g. from a site's FHIR
endpoint or bulk-export NDJSON) onto the canonical ``MeasurementIn`` schema, so
EHR data enters through exactly the same validation path as manual/CSV data.

Identity: the adapter never stores FHIR patient identifiers. The caller
supplies a site-held mapping {FHIR Patient reference -> study_id}; resources of
unmapped patients are skipped (not enrolled / no consent).
"""

from __future__ import annotations

from typing import Any, Iterable

from irtcri.hub.schemas import MeasurementIn
from irtcri.schemas.enums import DataSource

#: LOINC -> data-dictionary variable (extend per site after validation)
LOINC_MAP: dict[str, str] = {
    "8310-5": "temperature_c", "8867-4": "heart_rate", "59408-5": "spo2", "2708-6": "sao2",
    "8480-6": "sbp", "8462-4": "dbp", "8478-0": "map", "9279-1": "respiratory_rate", "3150-0": "fio2",
    "1963-8": "hco3", "2019-8": "paco2", "1920-8": "ast", "3094-0": "bun", "22664-7": "urea",
    "17861-6": "calcium", "2160-0": "creatinine", "1975-2": "bilirubin", "2345-7": "glucose",
    "2524-7": "lactate", "19123-9": "magnesium", "2777-1": "phosphate", "10839-9": "troponin_i",
    "4544-3": "hematocrit", "718-7": "haemoglobin", "3173-2": "ptt", "6690-2": "wbc", "777-3": "platelets",
    "1988-5": "crp", "33959-8": "procalcitonin",
}
UCUM_TO_DICT = {"Cel": "degC", "[degF]": "degF", "/min": "bpm", "%": "pct", "mm[Hg]": "mmHg", "mmol/L": "mmol/L",
                "umol/L": "umol/L", "mg/dL": "mg/dL", "g/L": "g/L", "g/dL": "g/dL", "10*9/L": "10^9/L",
                "10*3/uL": "10^3/uL", "U/L": "U/L", "s": "s", "ng/L": "ng/L", "ng/mL": "ng/mL", "mg/L": "mg/L",
                "kPa": "kPa", "1": "fraction"}


def observation_to_measurement(obs: dict[str, Any], patient_map: dict[str, str],
                               episode_code: str | None = None) -> MeasurementIn | None:
    if obs.get("resourceType") != "Observation" or obs.get("status") in ("entered-in-error", "cancelled"):
        return None
    ref = (obs.get("subject") or {}).get("reference")
    study_id = patient_map.get(ref or "")
    if study_id is None:
        return None
    var = None
    for c in (obs.get("code") or {}).get("coding", []):
        if c.get("system", "").endswith("loinc.org") and c.get("code") in LOINC_MAP:
            var = LOINC_MAP[c["code"]]
            break
    if var is None or "valueQuantity" not in obs:
        return None
    q = obs["valueQuantity"]
    unit = UCUM_TO_DICT.get(q.get("code") or q.get("unit") or "", q.get("unit"))
    if var == "heart_rate" or var == "respiratory_rate":
        unit = None if unit in ("bpm", "per_min") else unit
    event_time = obs.get("effectiveDateTime") or (obs.get("effectivePeriod") or {}).get("start")
    return MeasurementIn(study_id=study_id, episode_code=episode_code, variable=var, value=q.get("value"),
                         unit=unit, comparator=q.get("comparator"), event_time=event_time,
                         source_time=obs.get("issued"), source=DataSource.EHR,
                         source_ref=f"fhir:Observation/{obs.get('id')}",
                         loinc_code=next((c["code"] for c in obs["code"]["coding"] if c.get("code") in LOINC_MAP),
                                         None))


def map_bundle(resources: Iterable[dict], patient_map: dict[str, str]) -> tuple[list[MeasurementIn], int]:
    out, skipped = [], 0
    for r in resources:
        try:
            m = observation_to_measurement(r, patient_map)
        except ValueError:
            m = None
        if m is None:
            skipped += 1
        else:
            out.append(m)
    return out, skipped
