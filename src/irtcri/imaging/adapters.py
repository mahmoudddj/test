"""Acquisition adapters.

The platform never talks to camera hardware directly by default. Adapters
turn "something that produced files" into (bytes, filename, metadata) items
that go through the single ingestion service (``imaging.ingest.ingest_image``).

* ``FolderExportAdapter`` - watch folder / camera export directory (implemented)
* ``CameraSDKAdapter``    - interface for a future vendor-SDK or mobile-app
                            integration (e.g. FLIR ONE / Atlas SDK) - to be
                            implemented and validated per device; not required
                            for the MVP.

Metadata sources, in priority order:
1. sidecar JSON next to the file (``<file>.json``), written by the acquisition
   app or the operator;
2. filename convention ``<STUDYID>__<EPISODECODE>__<YYYYmmddTHHMMSS>__<view>__<modality>.<ext>``
   (local site time).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, Protocol

from irtcri.core.provenance import sha256_bytes

FILENAME_RE = re.compile(
    r"^(?P<study_id>[A-Za-z0-9-]+)__(?P<episode_id>[A-Za-z0-9-]+)__(?P<ts>\d{8}T\d{6})__"
    r"(?P<view>[a-z_]+)__(?P<modality>thermal|rgb)\.(?P<ext>[A-Za-z0-9]+)$")


@dataclass
class Capture:
    data: bytes
    filename: str
    modality: str
    meta: dict = field(default_factory=dict)
    source_path: str | None = None

    @property
    def sha256(self) -> str:
        return sha256_bytes(self.data)


class CaptureAdapter(Protocol):
    name: str

    def captures(self) -> Iterator[Capture]: ...


class CameraSDKAdapter(Protocol):
    """Future direct-device integration. Implementations must deliver the
    radiometric matrix (degC) plus calibration metadata; they must not alter pixel
    data beyond the vendor's documented radiometric conversion."""

    name: str

    def list_new_captures(self, since: datetime | None) -> list[str]: ...
    def fetch(self, capture_id: str) -> Capture: ...


def parse_filename(name: str) -> dict | None:
    m = FILENAME_RE.match(name)
    if not m:
        return None
    d = m.groupdict()
    d["acquisition_time"] = datetime.strptime(d.pop("ts"), "%Y%m%dT%H%M%S")  # naive local time
    d.pop("ext")
    return d


class FolderExportAdapter:
    name = "watch_folder"

    def __init__(self, folder: str | Path, extensions: tuple[str, ...] = (
            "csv", "txt", "npy", "tif", "tiff", "jpg", "jpeg", "png")):
        self.folder = Path(folder)
        self.extensions = extensions

    def captures(self) -> Iterator[Capture]:
        for p in sorted(self.folder.rglob("*")):
            if not p.is_file() or p.suffix.lower().lstrip(".") not in self.extensions:
                continue
            meta: dict = {}
            sidecar = p.with_name(p.name + ".json")
            if sidecar.exists():
                meta = json.loads(sidecar.read_text())
            else:
                parsed = parse_filename(p.name)
                if parsed is None:
                    yield Capture(b"", p.name, "unknown", {"error": "no sidecar JSON and filename does not "
                                                                    "match naming convention"}, str(p))
                    continue
                meta = parsed
            modality = meta.pop("modality", None) or ("thermal" if "thermal" in p.name.lower() else "rgb")
            yield Capture(p.read_bytes(), p.name, modality, meta, str(p))
