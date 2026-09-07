"""CCPA/CPRA ADMT rights over the real HTTP API.

Access (GET /api/account/export), Modification / rectification (PATCH
/api/account), Deletion (DELETE /api/account) and Transparency (audit trail +
privacy disclosures in the response of the export endpoint).
"""
from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient

import audit
import db
from main import app

client = TestClient(app)


def _fresh_user() -> tuple[str, str]:
    email = f"admt-{uuid.uuid4().hex[:10]}@fluxswarm.test"
    r = client.post("/api/auth/register", json={
        "email": email, "name": "Original Name", "password": "pw-12345678", "tos_accept": True,
    })
    assert r.status_code == 200, r.text
    tok = client.post("/api/auth/login", json={
        "email": email, "password": "pw-12345678",
    }).json()["token"]
    return email, tok


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_rectify_updates_name_and_me():
    _, tok = _fresh_user()
    r = client.patch("/api/account", json={"name": "  Corrected Name  "}, headers=_headers(tok))
    assert r.status_code == 200, r.text
    assert r.json()["user"]["name"] == "Corrected Name"
    assert client.get("/api/me", headers=_headers(tok)).json()["name"] == "Corrected Name"


def test_rectify_is_audited():
    email, tok = _fresh_user()
    client.patch("/api/account", json={"name": "Renamed"}, headers=_headers(tok))
    trail = audit.list_events() if hasattr(audit, "list_events") else _read_trail()
    assert any(e.get("event") == "account.rectify" and e.get("uid") ==
               db.get_user_by_email(email)["id"] for e in trail)


def _read_trail() -> list[dict]:
    entries = []
    with open(audit.AUDIT_FILE, encoding="utf-8") as f:
        for line in f:
            try:
                import json
                entries.append(json.loads(line))
            except ValueError:
                pass
    return entries


def test_rectify_rejects_empty_and_oversized():
    _, tok = _fresh_user()
    assert client.patch("/api/account", json={"name": "   "}, headers=_headers(tok)).status_code == 422
    assert client.patch("/api/account", json={"name": "x" * 81}, headers=_headers(tok)).status_code == 422


def test_rectify_requires_auth():
    r = client.patch("/api/account", json={"name": "X"}, headers=_headers("forged"))
    assert r.status_code == 401


def test_export_reflects_rectification():
    email, tok = _fresh_user()
    client.patch("/api/account", json={"name": "Exported Name"}, headers=_headers(tok))
    exp = client.get("/api/account/export", headers=_headers(tok))
    assert exp.status_code == 200, exp.text
    payload = exp.json()
    assert payload["user"]["email"] == email
    assert payload["user"]["name"] == "Exported Name"
    for section in ("projects", "referrals", "templates", "template_purchases"):
        assert section in payload


def test_delete_then_export_is_denied():
    email, tok = _fresh_user()
    r = client.delete("/api/account", headers=_headers(tok))
    assert r.status_code == 200, r.text
    assert client.get("/api/account/export", headers=_headers(tok)).status_code == 401
    assert db.get_user_by_email(email) is None