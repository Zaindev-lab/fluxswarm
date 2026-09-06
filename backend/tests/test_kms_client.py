"""Phase 3 — KMS envelope encryption + legacy Fernet fallback.

Covers (a) the hermetic file-KMS backend (round-trip, envelope v1 shape,
per-blob DEK freshness, malicious-tamper detection); (b) parse_envelope
validation; (c) cloud backends degrade to a loud KmsError when unconfigured
(never silent plaintext); (d) unknown backend raises; (e) vault rewrite:
new blobs are KMS envelopes, legacy Fernet blobs still decrypt.
"""
from __future__ import annotations

import json
import os

import pytest

import kms_client
import vault


# ---------------------------------------------------------------------------
# file backend round-trip + envelope shape
# ---------------------------------------------------------------------------
class TestFileKms:
    def test_encrypt_decrypt_roundtrip(self):
        c = kms_client.FileKms()
        blob = c.encrypt(b"sk-ant-secret-123")
        assert c.decrypt(blob) == b"sk-ant-secret-123"

    def test_envelope_is_v1_json_with_all_fields(self):
        c = kms_client.FileKms()
        blob = c.encrypt(b"tok")
        data = kms_client.parse_envelope(blob)
        assert data["v"] == 1
        assert data["kms"] == "file"
        assert data["key_ref"] == "file:derive(hkdf-sha256)"
        for key in ("wrapped_dek", "dek_nonce", "nonce", "ciphertext"):
            assert data[key], f"missing {key}"
        # ciphertext must never be raw/empty or carry the plaintext.
        assert "tok" not in data["ciphertext"]

    def test_fresh_dek_per_blob(self):
        c = kms_client.FileKms()
        a = c.encrypt(b"same-plaintext")
        b = c.encrypt(b"same-plaintext")
        assert a != b
        ja, jb = json.loads(a), json.loads(b)
        assert ja["ciphertext"] != jb["ciphertext"]
        assert ja["nonce"] != jb["nonce"]

    def test_tampered_ciphertext_rejected(self):
        c = kms_client.FileKms()
        blob = c.encrypt(b"tok")
        data = json.loads(blob)
        # Flip a base64 char -> GCM authentication must fail.
        ct = list(data["ciphertext"])
        ct[-1] = "A" if ct[-1] != "A" else "B"
        data["ciphertext"] = "".join(ct)
        with pytest.raises(kms_client.KmsError):
            c.decrypt(json.dumps(data))

    def test_encrypt_never_includes_plaintext(self):
        c = kms_client.FileKms()
        blob = c.encrypt(b"ULTRA-SECRET-TOKEN-XYZ")
        assert "ULTRA-SECRET-TOKEN-XYZ" not in blob


# ---------------------------------------------------------------------------
# parse_envelope validation
# ---------------------------------------------------------------------------
class TestParseEnvelope:
    def test_rejects_bad_version(self):
        with pytest.raises(ValueError):
            kms_client.parse_envelope('{"v": 99, "kms": "file"}')

    def test_rejects_missing_field(self):
        with pytest.raises(ValueError):
            kms_client.parse_envelope('{"v": 1, "kms": "file"}')

    def test_rejects_non_object(self):
        with pytest.raises(ValueError):
            kms_client.parse_envelope('[1,2,3]')


# ---------------------------------------------------------------------------
# cloud backends: loud, safe failure when unconfigured
# ---------------------------------------------------------------------------
class TestUnconfiguredCloudBackends:
    @pytest.mark.parametrize("factory,backend", [
        (kms_client._aws_kms_client, "aws_kms"),
        (kms_client._azure_keyvault_client, "azure_keyvault"),
        (kms_client._hashicorp_vault_client, "hashicorp_vault"),
    ])
    def test_encrypt_raises_when_unconfigured(self, factory, backend, monkeypatch):
        monkeypatch.delenv("FLUXSWARM_AWS_KMS_KEY_ID", raising=False)
        monkeypatch.delenv("FLUXSWARM_AZURE_KEYVAULT_URL", raising=False)
        monkeypatch.delenv("FLUXSWARM_AZURE_KEYVAULT_KEY", raising=False)
        monkeypatch.delenv("FLUXSWARM_VAULT_ADDR", raising=False)
        monkeypatch.delenv("FLUXSWARM_VAULT_TOKEN", raising=False)
        monkeypatch.delenv("FLUXSWARM_VAULT_TRANSIT_KEY", raising=False)
        c = factory()
        assert c.backend == backend
        assert "unconfigured" in c.key_ref()
        with pytest.raises(kms_client.KmsError):
            c.encrypt(b"tok")

    def test_unknown_backend_raises(self, monkeypatch):
        monkeypatch.setenv("FLUXSWARM_KMS_BACKEND", "not-a-backend")
        with pytest.raises(kms_client.KmsError):
            kms_client.get_client()


# ---------------------------------------------------------------------------
# vault: envelopes are the new store format, legacy Fernet still decrypts
# ---------------------------------------------------------------------------
class TestVaultKmsIntegration:
    def _vault_with_store(self, monkeypatch, tmp_path):
        store = tmp_path / "byok.json"
        monkeypatch.setattr(vault, "_STORE", store)
        return store

    def test_set_and_get_uses_envelope(self, monkeypatch, tmp_path):
        self._vault_with_store(monkeypatch, tmp_path)
        vault.set_user_key(7, "anthropic", "sk-ant-token-007")
        raw = vault._load().get("7", {}).get("anthropic")
        assert vault._is_envelope(raw)
        assert vault.get_user_key(7, "anthropic") == "sk-ant-token-007"

    def test_legacy_fernet_blob_still_decrypts(self, monkeypatch, tmp_path):
        from cryptography.fernet import Fernet
        store = self._vault_with_store(monkeypatch, tmp_path)
        key = Fernet.generate_key()
        monkeypatch.setattr(vault, "_FERNET", Fernet(key))
        old_blob = Fernet(key).encrypt(b"sk-legacy-token").decode()
        # Simulate a pre-KMS store row.
        import json
        store.write_text(json.dumps({"9": {"openai": old_blob}}), encoding="utf-8")
        assert vault.get_user_key(9, "openai") == "sk-legacy-token"

    def test_missing_key_returns_none(self, monkeypatch, tmp_path):
        self._vault_with_store(monkeypatch, tmp_path)
        assert vault.get_user_key(1, "kimi") is None