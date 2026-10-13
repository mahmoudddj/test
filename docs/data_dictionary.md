# IRT-in-CRI Data Dictionary (v1.0.0)

_Generated from `configs/data_dictionary.yaml` - do not edit by hand._

## Missing-value codes

* `ND` - Not done / not measured
* `NA` - Not applicable
* `UNK` - Unknown
* `NR` - Not recorded in source

Stored as NULL value with optional missing_reason code (ND|NA|UNK|NR). No sentinel numbers (e.g. -99) are ever stored.

## Entity: `patient`

| name | label | type | unit (alternatives) | hard range | plausible range | availability | interval h | required | source | time reference |
|---|---|---|---|---|---|---|---|---|---|---|
| `age_years` | Age at enrolment | int | years | [0.0, 120.0] | [18.0, 105.0] | event_time+0min |  | yes | case report form (date of birth is NOT stored) | enrolment date |
| `sex` | Sex | category | {female, male, other, unknown} |  |  | event_time+0min |  | yes | case report form | enrolment |
| `bmi` | Body mass index | float | kg/m2 | [8.0, 90.0] | [13.0, 60.0] | event_time+0min |  |  | case report form | enrolment |
| `diabetes` | Diabetes mellitus | bool |  |  |  | event_time+0min |  | yes | case report form / EHR problem list | enrolment |
| `chronic_kidney_disease` | Chronic kidney disease | bool |  |  |  | event_time+0min |  | yes | case report form | enrolment |
| `on_dialysis` | Receiving haemodialysis at enrolment | bool |  |  |  | event_time+0min |  | yes | case report form | enrolment |
| `immunosuppressed` | Immunosuppression (drugs or condition) | bool |  |  |  | event_time+0min |  | yes | case report form | enrolment |

## Entity: `observation`

| name | label | type | unit (alternatives) | hard range | plausible range | availability | interval h | required | source | time reference |
|---|---|---|---|---|---|---|---|---|---|---|
| `temperature_c` | Body temperature | float | degC (degF) | [25.0, 45.0] | [34.0, 41.5] | event_time+0min | 6.0 | yes | nursing chart / bedside monitor / EHR | measurement time |
| `heart_rate` | Heart rate | float | bpm | [0.0, 300.0] | [30.0, 200.0] | event_time+0min | 6.0 | yes | bedside monitor / nursing chart | measurement time |
| `spo2` | Oxygen saturation (pulse oximetry) | float | pct | [0.0, 100.0] | [60.0, 100.0] | event_time+0min | 6.0 | yes | bedside monitor | measurement time |
| `sbp` | Systolic blood pressure | float | mmHg | [0.0, 300.0] | [50.0, 250.0] | event_time+0min | 6.0 | yes | bedside monitor / nursing chart | measurement time |
| `dbp` | Diastolic blood pressure | float | mmHg | [0.0, 250.0] | [20.0, 150.0] | event_time+0min | 6.0 | yes | bedside monitor / nursing chart | measurement time |
| `map` | Mean arterial pressure | float | mmHg | [0.0, 250.0] | [30.0, 180.0] | event_time+0min | 6.0 |  | bedside monitor or derived (dbp + (sbp-dbp)/3) | measurement time |
| `respiratory_rate` | Respiratory rate | float | per_min | [0.0, 80.0] | [6.0, 50.0] | event_time+0min | 6.0 | yes | nursing chart / bedside monitor | measurement time |
| `fio2` | Fraction of inspired oxygen | float | fraction (pct) | [0.21, 1.0] | [0.21, 1.0] | event_time+0min | 6.0 |  | ventilator / oxygen prescription | time of setting |
| `apache_ii` | APACHE II score | int | points | [0.0, 71.0] | [0.0, 60.0] | event_time+0min |  |  | ICU documentation (worst values of first 24 h) | end of the 24 h scoring window |

## Entity: `laboratory`

| name | label | type | unit (alternatives) | hard range | plausible range | availability | interval h | required | source | time reference |
|---|---|---|---|---|---|---|---|---|---|---|
| `hco3` | Bicarbonate (HCO3-) | float | mmol/L | [0.0, 60.0] | [8.0, 45.0] | source_time+30min | 6.0 |  | blood gas analyser / laboratory | specimen collection time |
| `paco2` | Arterial partial pressure of CO2 | float | mmHg (kPa) | [5.0, 200.0] | [15.0, 120.0] | source_time+30min | 6.0 |  | blood gas analyser | specimen collection time |
| `sao2` | Arterial oxygen saturation (blood gas) | float | pct | [0.0, 100.0] | [50.0, 100.0] | source_time+30min | 6.0 |  | blood gas analyser | specimen collection time |
| `lactate` | Lactate | float | mmol/L (mg/dL) | [0.0, 35.0] | [0.2, 20.0] | source_time+30min | 6.0 |  | blood gas analyser / laboratory | specimen collection time |
| `ast` | Aspartate aminotransferase | float | U/L | [0.0, 30000.0] | [3.0, 5000.0] | source_time+240min | 24.0 |  | laboratory | specimen collection time |
| `bun` | Blood urea nitrogen | float | mg/dL (mmol/L) | [0.0, 400.0] | [2.0, 200.0] | source_time+240min | 24.0 |  | laboratory | specimen collection time |
| `urea` | Urea | float | mmol/L (mg/dL) | [0.0, 150.0] | [1.0, 80.0] | source_time+240min | 24.0 |  | laboratory | specimen collection time |
| `calcium` | Calcium (total) | float | mmol/L (mg/dL) | [0.5, 6.0] | [1.2, 3.5] | source_time+240min | 24.0 |  | laboratory | specimen collection time |
| `creatinine` | Creatinine | float | umol/L (mg/dL) | [5.0, 3500.0] | [20.0, 2000.0] | source_time+240min | 24.0 | yes | laboratory | specimen collection time |
| `bilirubin` | Bilirubin (total) | float | umol/L (mg/dL) | [0.0, 1200.0] | [1.0, 600.0] | source_time+240min | 24.0 |  | laboratory | specimen collection time |
| `glucose` | Glucose | float | mmol/L (mg/dL) | [0.3, 100.0] | [2.0, 40.0] | source_time+30min | 6.0 |  | laboratory / point of care | specimen collection time |
| `magnesium` | Magnesium | float | mmol/L (mg/dL) | [0.1, 6.0] | [0.3, 3.0] | source_time+240min | 24.0 |  | laboratory | specimen collection time |
| `phosphate` | Phosphate | float | mmol/L (mg/dL) | [0.1, 8.0] | [0.3, 5.0] | source_time+240min | 24.0 |  | laboratory | specimen collection time |
| `troponin_i` | Troponin I (high-sensitivity) | float | ng/L (ng/mL) | [0.0, 500000.0] | [0.0, 50000.0] | source_time+120min |  |  | laboratory | specimen collection time |
| `hematocrit` | Haematocrit | float | pct (L/L) | [5.0, 80.0] | [12.0, 65.0] | source_time+120min | 24.0 |  | laboratory | specimen collection time |
| `haemoglobin` | Haemoglobin | float | g/L (g/dL) | [20.0, 260.0] | [40.0, 200.0] | source_time+120min | 24.0 | yes | laboratory | specimen collection time |
| `ptt` | Partial thromboplastin time (aPTT) | float | s | [5.0, 250.0] | [15.0, 150.0] | source_time+120min | 24.0 |  | laboratory | specimen collection time |
| `wbc` | White blood cell count | float | 10^9/L (10^3/uL) | [0.0, 600.0] | [0.3, 100.0] | source_time+120min | 24.0 | yes | laboratory | specimen collection time |
| `platelets` | Platelet count | float | 10^9/L (10^3/uL) | [0.0, 3000.0] | [5.0, 1500.0] | source_time+120min | 24.0 | yes | laboratory | specimen collection time |
| `crp` | C-reactive protein | float | mg/L (mg/dL) | [0.0, 1000.0] | [0.0, 500.0] | source_time+240min | 24.0 |  | laboratory | specimen collection time |
| `procalcitonin` | Procalcitonin | float | ng/mL | [0.0, 1000.0] | [0.0, 200.0] | source_time+240min |  |  | laboratory | specimen collection time |

## Entity: `assessment`

| name | label | type | unit (alternatives) | hard range | plausible range | availability | interval h | required | source | time reference |
|---|---|---|---|---|---|---|---|---|---|---|
| `erythema_mm` | Exit-site erythema (largest diameter) | float | mm | [0.0, 300.0] | [0.0, 100.0] | event_time+0min | 24.0 | yes | bedside assessment (study CRF) | assessment time |
| `induration_mm` | Exit-site induration (largest diameter) | float | mm | [0.0, 300.0] | [0.0, 100.0] | event_time+0min | 24.0 | yes | bedside assessment | assessment time |
| `tenderness` | Exit-site tenderness | bool |  |  |  | event_time+0min |  | yes | bedside assessment | assessment time |
| `purulent_discharge` | Purulent discharge at exit site | bool |  |  |  | event_time+0min |  | yes | bedside assessment | assessment time |

## Entity: `image`

| name | label | type | unit (alternatives) | hard range | plausible range | availability | interval h | required | source | time reference |
|---|---|---|---|---|---|---|---|---|---|---|
| `ambient_temperature_c` | Ambient room temperature at acquisition | float | degC | [0.0, 50.0] | [17.0, 32.0] | event_time+0min |  | yes | room thermometer / hygrometer | image acquisition time |
| `relative_humidity_pct` | Relative humidity at acquisition | float | pct | [0.0, 100.0] | [15.0, 85.0] | event_time+0min |  |  | hygrometer | image acquisition time |
| `distance_cm` | Camera-to-skin distance | float | cm | [1.0, 500.0] | [15.0, 100.0] | event_time+0min |  | yes | operator (protocol target distance) | image acquisition time |
| `emissivity` | Emissivity setting | float | ratio | [0.1, 1.0] | [0.95, 0.99] | event_time+0min |  | yes | camera settings (human skin approx. 0.98) | image acquisition time |
