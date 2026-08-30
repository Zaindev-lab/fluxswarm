"""Legal pages render the operating-entity disclosure from env config.

The four pages (/privacy, /privacy-en, /terms, /terms-en) must show the company
block when FLUXSWARM_LEGAL_* is set and omit it (gracefully) when it is not.
Env is overridden per-module and restored, so no other test module leaks state.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

_LEGAL_ENV = {
    "FLUXSWARM_LEGAL_ENTITY": "AI FOR SAAS (Single-Member LLC)",
    "FLUXSWARM_LEGAL_REGISTRY_NO": "128107050805626155",
    "FLUXSWARM_LEGAL_TAX_ID": "181310805611700880",
    "FLUXSWARM_LEGAL_ADDRESS": "Bechar Centre ville, Algeria",
    "FLUXSWARM_LEGAL_PHONE": "+213771070342",
    "FLUXSWARM_CONTACT_EMAIL": "aiforsaasdz@outlook.fr",
}


@pytest.fixture(scope="module", autouse=True)
def _legal_env():
    saved = {k: os.environ.get(k) for k in _LEGAL_ENV}
    for k, v in _LEGAL_ENV.items():
        os.environ[k] = v
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.mark.parametrize("path", ["/privacy", "/privacy-en", "/terms", "/terms-en"])
def test_legal_pages_show_entity_block(path):
    r = client.get(path)
    assert r.status_code == 200, path
    for needle in ("AI FOR SAAS", "128107050805626155", "181310805611700880",
                   "Bechar", "+213771070342", "aiforsaasdz@outlook.fr"):
        assert needle in r.text, f"{path} missing {needle}"


@pytest.mark.parametrize("path", ["/privacy", "/privacy-en", "/terms", "/terms-en"])
def test_legal_pages_serve_base(path):
    r = client.get(path)
    assert r.status_code == 200
    assert "<html" in r.text and "</html>" in r.text


def test_entity_block_hidden_when_unset():
    saved = {k: os.environ.get(k) for k in _LEGAL_ENV}
    for k in _LEGAL_ENV:
        os.environ.pop(k, None)
    try:
        r = client.get("/privacy-en")
        assert r.status_code == 200
        assert "Operating entity" not in r.text
        assert "AI FOR SAAS" not in r.text
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v