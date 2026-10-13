# Sample data and import templates

Everything here is **SYNTHETIC**. Never place real participant data in this repository.

* `templates/` - CSV templates for `irtcri import <kind> <file> --site S01` (dry run by default,
  add `--commit` after reviewing the issue report). Column names are the canonical field
  names of `src/irtcri/hub/schemas.py`; measurement columns may use data-dictionary names or
  aliases, optionally with a unit in brackets (`creatinine [mg/dL]`). Naive timestamps are
  interpreted in the site's registered timezone unless `--timezone` is given.
* `watch_folder_example/` - layout expected by `irtcri watch-folder`: either a sidecar JSON
  per image or the filename convention
  `<STUDYID>__<EPISODECODE>__<YYYYmmddTHHMMSS>__<view>__<thermal|rgb>.<ext>`.
* Realistic multi-site synthetic data for demos: `irtcri synthetic --n-patients 60`
  or `scripts/run_demo.sh`.
