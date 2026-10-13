"""Image readers.

We deliberately do NOT reverse-engineer proprietary camera formats. Supported:

Thermal (radiometric = per-pixel temperature available):
  * ``.csv`` / ``.txt``  temperature matrix exported by vendor software
                         (e.g. FLIR Thermal Studio / FLIR Tools "export CSV")
  * ``.npy``             float temperature array in degC
  * ``.tif/.tiff``       16-bit or float TIFF; 16-bit counts converted with a
                         *declared* linear scale (``tiff_scale``, ``tiff_offset``),
                         e.g. centi-Kelvin exports
  * radiometric JPEG     via an optional plugin adapter (see ``RADIOMETRIC_PLUGINS``),
                         e.g. a site-validated wrapper around the vendor SDK or
                         flirimageextractor. Not enabled by default.
Thermal (non-radiometric): palette-rendered ``.jpg/.png`` - accepted but flagged
  WARNING because absolute temperatures are unavailable.
RGB: ``.jpg/.jpeg/.png/.tif``.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import numpy as np
from PIL import ExifTags, Image

THERMAL_RADIOMETRIC_EXT = {"csv", "txt", "npy", "tif", "tiff"}
IMAGE_EXT = {"jpg", "jpeg", "png", "tif", "tiff"}


class ImageReadError(ValueError):
    pass


@dataclass
class DecodedImage:
    modality: str
    file_format: str
    width: int
    height: int
    temperature_c: np.ndarray | None = None     # HxW float32 (thermal radiometric)
    pixels: np.ndarray | None = None            # HxWxC uint8 (RGB or palette thermal)
    is_radiometric: bool = False
    exif_time: datetime | None = None
    exif: dict = field(default_factory=dict)


RadiometricPlugin = Callable[[bytes], np.ndarray]
#: Registry for site-validated radiometric decoders keyed by extension/vendor tag.
RADIOMETRIC_PLUGINS: dict[str, RadiometricPlugin] = {}


def _ext(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _exif(img: Image.Image) -> tuple[dict, datetime | None]:
    out: dict = {}
    try:
        raw = img.getexif()
    except Exception:
        return out, None
    for k, v in raw.items():
        name = ExifTags.TAGS.get(k, str(k))
        if isinstance(v, (str, int, float)):
            out[name] = v
    try:
        sub = raw.get_ifd(0x8769)
        for k, v in sub.items():
            name = ExifTags.TAGS.get(k, str(k))
            if isinstance(v, (str, int, float)):
                out[name] = v
    except Exception:
        pass
    ts = out.get("DateTimeOriginal") or out.get("DateTime")
    dt = None
    if isinstance(ts, str):
        try:
            dt = datetime.strptime(ts.strip(), "%Y:%m:%d %H:%M:%S")   # naive: camera local time
        except ValueError:
            dt = None
    return out, dt


def read_thermal(data: bytes, filename: str, tiff_scale: float | None = None,
                 tiff_offset: float = 0.0, plugin: str | None = None) -> DecodedImage:
    ext = _ext(filename)
    try:
        if plugin:
            if plugin not in RADIOMETRIC_PLUGINS:
                raise ImageReadError(f"radiometric plugin '{plugin}' not registered")
            arr = RADIOMETRIC_PLUGINS[plugin](data).astype(np.float32)
            return DecodedImage("thermal", ext, arr.shape[1], arr.shape[0], temperature_c=arr, is_radiometric=True)
        if ext in ("csv", "txt"):
            text = data.decode("utf-8-sig")
            rows = [r for r in text.splitlines() if r.strip()]
            sample = rows[-1] if rows else ""
            # vendor exports use ';' (often with decimal commas), tab or ','
            delim = ";" if ";" in sample else ("\t" if "\t" in sample else ",")
            # vendor exports may contain header lines; keep only rows that parse as numbers
            parsed = []
            for r in rows:
                cells = [c.strip().replace(",", ".") if delim == ";" else c.strip() for c in r.split(delim)]
                try:
                    parsed.append([float(c) for c in cells if c != ""])
                except ValueError:
                    continue
            if not parsed:
                raise ImageReadError("no numeric temperature rows found")
            widths = {len(r) for r in parsed}
            if len(widths) != 1:
                raise ImageReadError(f"ragged temperature matrix (row widths {sorted(widths)[:5]})")
            arr = np.asarray(parsed, dtype=np.float32)
            return DecodedImage("thermal", ext, arr.shape[1], arr.shape[0], temperature_c=arr, is_radiometric=True)
        if ext == "npy":
            arr = np.load(io.BytesIO(data), allow_pickle=False).astype(np.float32)
            if arr.ndim != 2:
                raise ImageReadError(f"expected 2-D temperature array, got shape {arr.shape}")
            return DecodedImage("thermal", ext, arr.shape[1], arr.shape[0], temperature_c=arr, is_radiometric=True)
        img = Image.open(io.BytesIO(data))
        img.load()
        exif, exif_dt = _exif(img)
        if ext in ("tif", "tiff") and img.mode in ("I;16", "I;16B", "I", "F"):
            arr = np.asarray(img, dtype=np.float32)
            if img.mode != "F":
                if tiff_scale is None:
                    raise ImageReadError("16-bit TIFF requires a declared tiff_scale (e.g. 0.01 for centi-degC)")
                arr = arr * tiff_scale + tiff_offset
            return DecodedImage("thermal", ext, arr.shape[1], arr.shape[0], temperature_c=arr,
                                is_radiometric=True, exif_time=exif_dt, exif=exif)
        rgb = np.asarray(img.convert("RGB"))
        return DecodedImage("thermal", ext, rgb.shape[1], rgb.shape[0], pixels=rgb, is_radiometric=False,
                            exif_time=exif_dt, exif=exif)
    except ImageReadError:
        raise
    except Exception as e:  # corrupted / unreadable
        raise ImageReadError(f"cannot decode thermal file '{filename}': {e}") from e


def read_rgb(data: bytes, filename: str) -> DecodedImage:
    ext = _ext(filename)
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        exif, exif_dt = _exif(img)
        rgb = np.asarray(img.convert("RGB"))
    except Exception as e:
        raise ImageReadError(f"cannot decode RGB file '{filename}': {e}") from e
    return DecodedImage("rgb", ext, rgb.shape[1], rgb.shape[0], pixels=rgb, exif_time=exif_dt, exif=exif)
