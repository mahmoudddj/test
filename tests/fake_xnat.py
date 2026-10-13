"""In-memory mock of the subset of the XNAT REST API used by irtcri.integrations.xnat.

Mirrors XNAT behaviour relevant to the integration: authentication (basic + JSESSION), 404 for
unknown projects, idempotent PUT of subjects/experiments/scans/resources, 409 on re-upload of an
existing file without ``overwrite=true``, ResultSet listings, custom fields on experiments.
"""

from __future__ import annotations

import base64
import hashlib
import re
from urllib.parse import unquote

import httpx


class FakeXNAT:
    def __init__(self, project: str = "IRTCRI", user: str = "svc", password: str = "pw"):
        self.projects = {project}
        self.user, self.password = user, password
        self.subjects: dict[tuple, dict] = {}
        self.experiments: dict[tuple, dict] = {}
        self.scans: dict[tuple, dict] = {}
        self.resources: set[tuple] = set()
        self.files: dict[tuple, bytes] = {}
        self.calls: list[tuple[str, str]] = []
        self.sessions_issued = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    # ------------------------------------------------------------------ handler
    def _authed(self, req: httpx.Request) -> bool:
        if "JSESSIONID=FAKE-SESSION" in req.headers.get("cookie", ""):
            return True
        auth = req.headers.get("authorization", "")
        if auth.startswith("Basic "):
            u, _, p = base64.b64decode(auth[6:]).decode().partition(":")
            return u == self.user and p == self.password
        return False

    def handle(self, req: httpx.Request) -> httpx.Response:
        path = unquote(req.url.path)
        self.calls.append((req.method, path))
        if not self._authed(req):
            return httpx.Response(401, text="unauthorised")
        if path == "/data/JSESSION":
            if req.method == "POST":
                self.sessions_issued += 1
                return httpx.Response(200, text="FAKE-SESSION")
            return httpx.Response(200)
        q = dict(req.url.params)
        m = re.match(r"^/data/projects/([^/]+)(.*)$", path)
        if not m:
            return httpx.Response(404)
        project, rest = m.groups()
        if project not in self.projects:
            return httpx.Response(404, text="project not found")
        if rest == "":
            return httpx.Response(200, json={"items": [{"data_fields": {"ID": project}}]})
        if rest == "/subjects":
            return self._rs([{"label": s[1], "ID": f"XNAT_S{i}"}
                             for i, s in enumerate(self.subjects) if s[0] == project])
        if rest == "/experiments":
            return self._rs([{"label": e[2], "subject_label": e[1], "xsiType": v["xsiType"]}
                             for e, v in self.experiments.items() if e[0] == project])
        m = re.match(r"^/subjects/([^/]+)(.*)$", rest)
        subj, rest = m.groups()
        skey = (project, subj)
        if rest == "":
            if req.method == "PUT":
                self.subjects.setdefault(skey, {}).update(q)
                return httpx.Response(200)
            return httpx.Response(200 if skey in self.subjects else 404)
        if skey not in self.subjects:
            return httpx.Response(404, text="subject not found")
        if rest == "/experiments":
            return self._rs([{"label": e[2], "xsiType": v["xsiType"]} for e, v in self.experiments.items()
                             if e[:2] == skey])
        m = re.match(r"^/experiments/([^/]+)(.*)$", rest)
        exp, rest = m.groups()
        ekey = (project, subj, exp)
        if rest == "":
            if req.method == "PUT":
                if "xsiType" not in q and ekey not in self.experiments:
                    return httpx.Response(400, text="xsiType required")
                e = self.experiments.setdefault(ekey, {"xsiType": q.get("xsiType"), "fields": {}})
                for k, v in q.items():
                    fm = re.search(r"fields/field\[name=([^\]]+)\]/field$", k)
                    if fm:
                        e["fields"][fm.group(1).lower()] = v
                return httpx.Response(200)
            if ekey not in self.experiments:
                return httpx.Response(404)
            e = self.experiments[ekey]
            return httpx.Response(200, json={"items": [{"data_fields": {"label": exp}, "children": [
                {"field": "fields/field", "items": [{"data_fields": {"name": k, "field": v}}
                                                    for k, v in e["fields"].items()]}]}]})
        if ekey not in self.experiments:
            return httpx.Response(404, text="experiment not found")
        if rest == "/scans":
            return self._rs([{"ID": k[3], "type": v.get("type")} for k, v in self.scans.items() if k[:3] == ekey])
        parent = ekey
        m = re.match(r"^/scans/([^/]+)(.*)$", rest)
        if m:
            scan, rest = m.groups()
            parent = ekey + (scan,)
            if rest == "":
                if req.method == "PUT":
                    xsi = q.get("xsiType", "")
                    self.scans[parent] = {"type": q.get(f"{xsi}/type")}
                    return httpx.Response(200)
                return httpx.Response(200 if parent in self.scans else 404)
            if parent not in self.scans:
                return httpx.Response(404, text="scan not found")
        if rest == "/resources":
            return self._rs([{"label": r[-1]} for r in self.resources if r[:-1] == parent])
        m = re.match(r"^/resources/([^/]+)(.*)$", rest)
        if not m:
            return httpx.Response(404)
        res, rest = m.groups()
        rkey = parent + (res,)
        if rest == "":
            if req.method == "PUT":
                if rkey in self.resources:
                    return httpx.Response(409, text="resource exists")
                self.resources.add(rkey)
                return httpx.Response(200)
            return httpx.Response(200 if rkey in self.resources else 404)
        if rkey not in self.resources:
            return httpx.Response(404, text="resource not found")
        if rest == "/files":
            return self._rs([{"Name": k[-1], "Size": len(v), "URI": "/".join(k),
                              "digest": hashlib.md5(v).hexdigest()} for k, v in self.files.items()
                             if k[:-1] == rkey])
        m = re.match(r"^/files/(.+)$", rest)
        fkey = rkey + (m.group(1),)
        if req.method == "PUT":
            if fkey in self.files and q.get("overwrite") != "true":
                return httpx.Response(409, text="file exists")
            self.files[fkey] = req.content
            return httpx.Response(200)
        if req.method == "GET":
            return httpx.Response(200, content=self.files[fkey]) if fkey in self.files else httpx.Response(404)
        return httpx.Response(405)

    @staticmethod
    def _rs(rows: list[dict]) -> httpx.Response:
        return httpx.Response(200, json={"ResultSet": {"Result": rows, "totalRecords": str(len(rows))}})

    def file_names(self, resource: str) -> list[str]:
        return sorted(k[-1] for k in self.files if k[-2] == resource)
