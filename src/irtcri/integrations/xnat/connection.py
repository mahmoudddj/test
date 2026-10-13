"""Authenticated HTTP session to an XNAT server.

* credentials only from settings / environment (XNAT_USERNAME / XNAT_PASSWORD)
* a JSESSION is opened once (``POST /data/JSESSION``) and reused; basic auth is the fallback
* error messages never include credentials or request bodies
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from irtcri.core.config import Settings, get_settings


class XNATError(RuntimeError):
    def __init__(self, status: int, method: str, path: str, detail: str = ""):
        super().__init__(f"XNAT {method} {path} -> HTTP {status} {detail[:200]}")
        self.status = status


@dataclass(frozen=True)
class XNATConfig:
    url: str
    username: str
    password: str
    project_id: str
    verify_tls: bool = True
    session_xsi_type: str = "xnat:otherDicomSessionData"
    scan_xsi_type: str = "xnat:otherDicomScanData"
    timeout_s: float = 120.0

    @classmethod
    def from_settings(cls, s: Settings | None = None) -> "XNATConfig":
        s = s or get_settings()
        if not (s.xnat_url and s.xnat_username and s.xnat_password and s.xnat_project_id):
            raise ValueError("XNAT configuration incomplete "
                             "(XNAT_URL / XNAT_USERNAME / XNAT_PASSWORD / XNAT_PROJECT_ID)")
        return cls(url=s.xnat_url.rstrip("/"), username=s.xnat_username,
                   password=s.xnat_password.get_secret_value(), project_id=s.xnat_project_id,
                   verify_tls=s.xnat_verify_tls, session_xsi_type=s.xnat_session_xsi_type,
                   scan_xsi_type=s.xnat_scan_xsi_type)

    def __repr__(self) -> str:   # never leak the password into logs/tracebacks
        return f"XNATConfig(url={self.url!r}, username={self.username!r}, project_id={self.project_id!r})"


class XNATConnection:
    def __init__(self, cfg: XNATConfig, transport: httpx.BaseTransport | None = None):
        self.cfg = cfg
        self.client = httpx.Client(base_url=cfg.url, auth=(cfg.username, cfg.password), verify=cfg.verify_tls,
                                   timeout=cfg.timeout_s, transport=transport, follow_redirects=True)
        self._jsession: str | None = None

    def login(self) -> None:
        r = self.client.post("/data/JSESSION")
        if r.status_code >= 400:
            raise XNATError(r.status_code, "POST", "/data/JSESSION", "authentication failed")
        self._jsession = r.text.strip()
        self.client.cookies.set("JSESSIONID", self._jsession)

    def request(self, method: str, path: str, *, params: dict[str, Any] | None = None, content: bytes | None = None,
                headers: dict | None = None, ok: tuple[int, ...] = ()) -> httpx.Response:
        if self._jsession is None:
            self.login()
        r = self.client.request(method, path, params=params, content=content, headers=headers)
        if r.status_code >= 400 and r.status_code not in ok:
            raise XNATError(r.status_code, method, path, r.text if r.headers.get("content-type", "").startswith(
                "text/plain") else "")
        return r

    def get_json(self, path: str, params: dict | None = None) -> Any:
        return self.request("GET", path, params={"format": "json", **(params or {})}).json()

    def result_set(self, path: str, params: dict | None = None) -> list[dict]:
        """Rows of an XNAT ``ResultSet`` listing (``{"ResultSet": {"Result": [...]}}``)."""
        return self.get_json(path, params).get("ResultSet", {}).get("Result", [])

    def close(self) -> None:
        try:
            if self._jsession:
                self.client.delete("/data/JSESSION")
        finally:
            self.client.close()
