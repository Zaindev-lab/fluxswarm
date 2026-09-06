"""KMS-backed envelope encryption for BYOK provider keys (Phase 3).

Every user-supplied provider key is stored as an *envelope*: the plaintext is
sealed with a fresh, random per-blob Data Encryption Key (DEK, AES-256-GCM),
and the DEK itself is *wrapped* by a Key Encryption Key (KEK) that lives in a
managed KMS — never in our store. Attacking the ciphertext therefore requires
the KMS, not just the database/Vault layer.

Backends (selected by ``FLUXSWARM_KMS_BACKEND``):

  * ``file``          (DEFAULT, hermetic) — KEK derived from
                      ``FLUXSWARM_KMS_FILE_KEY`` (fallback: the existing
                      ``FLUXSWARM_FERNET_KEY``, then a generated user-local key
                      in Demo/dev). Perfect for tests, local dev and single-node
                      deployments; all other backends are for production.
  * ``aws_kms``       — AWS KMS ``GenerateDataKey``/``Decrypt``. Requires
                      ``FLUXSWARM_AWS_KMS_KEY_ID`` + AWS credentials.
  * ``azure_keyvault``— Azure Key Vault ``wrap_key``/``unwrap_key`` (RSA-OAEP).
                      Requires ``FLUXSWARM_AZURE_KEYVAULT_URL`` +
                      ``FLUXSWARM_AZURE_KEYVAULT_KEY`` + DefaultAzureCredential.
  * ``hashicorp_vault``— Vault Transit ``encrypt_data``/``decrypt_data``.
                      Requires ``FLUXSWARM_VAULT_ADDR`` +
                      ``FLUXSWARM_VAULT_TOKEN`` + ``FLUXSWARM_VAULT_TRANSIT_KEY``.

Envelope (v1):
    {
      "v": 1,
      "kms": "<backend>",
      "key_ref": "<KEK reference used to wrap the DEK>",
      "wrapped_dek": "<b64>",   // DEK encrypted by the KEK
      "dek_nonce": "<b64>",     // GCM nonce used to wrap the DEK
      "nonce": "<b64>",         // GCM nonce sealing the plaintext
      "ciphertext": "<b64>"     // AES-256-GCM(DEK, nonce, plaintext)
    }

Cloud SDKs are imported lazily so this module and the whole backend import and
run without any cloud packages installed.
"""
from __future__ import annotations

import base64
import json
import os
from abc import ABC, abstractmethod
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ENVELOPE_VERSION = 1

_DEMO_FLAGS = ("1", "true", "yes")


def _is_demo_mode() -> bool:
    return os.environ.get("FLUXSWARM_DEMO_MODE", "").strip().lower() in _DEMO_FLAGS


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def b64e(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def b64d(s: str) -> bytes:
    return base64.b64decode(s)


def parse_envelope(raw: str) -> dict:
    """Parse + validate an envelope string. Raises ValueError when malformed."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("envelope is not a JSON object")
    if data.get("v") != ENVELOPE_VERSION:
        raise ValueError(f"unsupported envelope version: {data.get('v')!r}")
    for key in ("kms", "key_ref", "wrapped_dek", "dek_nonce", "nonce", "ciphertext"):
        if not isinstance(data.get(key), str) or not data[key]:
            raise ValueError(f"envelope missing/invalid field: {key}")
    return data


def format_envelope(kms: str, key_ref: str, wrapped_dek: bytes,
                    dek_nonce: bytes, nonce: bytes, ciphertext: bytes) -> str:
    return json.dumps({
        "v": ENVELOPE_VERSION,
        "kms": kms,
        "key_ref": key_ref,
        "wrapped_dek": b64e(wrapped_dek),
        "dek_nonce": b64e(dek_nonce),
        "nonce": b64e(nonce),
        "ciphertext": b64e(ciphertext),
    }, sort_keys=True)


class KmsError(RuntimeError):
    """KMS operation failed (unwrap, wrap, or missing configuration)."""


class KmsClient(ABC):
    """Environment-agnostic envelope encryptor.

    Subclasses implement ``wrap_dek`` / ``unwrap_dek``; ``encrypt`` /
    ``decrypt`` implement the shared envelope protocol on top of them.
    """

    backend: str = "abstract"

    @abstractmethod
    def key_ref(self) -> str:
        """Human/ops-readable reference of the KEK bound to this client."""

    @abstractmethod
    def wrap_dek(self, dek: bytes) -> tuple[bytes, bytes]:
        """Encrypt a DEK with the KEK; return (wrapped_dek, gcm_nonce)."""

    @abstractmethod
    def unwrap_dek(self, wrapped: bytes, nonce: bytes) -> bytes:
        """Recover the DEK from ``wrapped`` (+ wrap nonce where applicable)."""

    def encrypt(self, data: bytes) -> str:
        """Seal ``data`` under a fresh DEK wrapped by this client's KEK."""
        dek = AESGCM.generate_key(bit_length=256)
        wrapped, dek_nonce = self.wrap_dek(dek)
        nonce = os.urandom(12)
        ciphertext = AESGCM(dek).encrypt(nonce, data, None)
        return format_envelope(self.backend, self.key_ref(), wrapped, dek_nonce, nonce, ciphertext)

    def decrypt(self, enveloped: str) -> bytes:
        """Open an envelope produced by ``encrypt`` (any compatible backend)."""
        try:
            data = parse_envelope(enveloped)
            dek = self.unwrap_dek(b64d(data["wrapped_dek"]), b64d(data["dek_nonce"]))
            return AESGCM(dek).decrypt(b64d(data["nonce"]), b64d(data["ciphertext"]), None)
        except KmsError:
            raise
        except Exception as exc:
            raise KmsError(f"envelope decrypt failed: {exc}") from exc


# ---------------------------------------------------------------------------
# file backend (default, hermetic)
# ---------------------------------------------------------------------------

def _derive_key(secret: str) -> bytes:
    """HKDF-SHA256(secret) -> 32-byte AES key from any passphrase material."""
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"fluxswarm-kms-file",
    ).derive(secret.encode("utf-8"))


class FileKms(KmsClient):
    """KEK = a local secret (env or file). Wraps DEKs with AES-256-GCM.

    Key precedence:
      1. FLUXSWARM_KMS_FILE_KEY (recommended; operator-provided)
      2. FLUXSWARM_FERNET_KEY  (existing BYOK secret — smooth migration)
      3. Demo/dev only: ~/.fluxswarm/kms_file.key (generated+persisted),
         else a throwaway ephemeral key.
    """

    backend = "file"

    _FILE_KEY_PATH = Path.home() / ".fluxswarm" / "kms_file.key"

    def __init__(self):
        self._kek = self._load_kek()

    def _load_kek(self) -> bytes:
        raw = _env("FLUXSWARM_KMS_FILE_KEY")
        if raw:
            return _derive_key(raw)
        raw = _env("FLUXSWARM_FERNET_KEY")
        if raw:
            return _derive_key(raw)
        if not _is_demo_mode():
            raise KmsError(
                "no KMS file key: set FLUXSWARM_KMS_FILE_KEY (or upgrade to a "
                "real KMS: FLUXSWARM_KMS_BACKEND=aws_kms|azure_keyvault|"
                "hashicorp_vault). Production must never use an ephemeral key."
            )
        try:
            self._FILE_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
            if self._FILE_KEY_PATH.exists():
                return _derive_key(self._FILE_KEY_PATH.read_text().strip())
            secret = os.urandom(32).hex()
            self._FILE_KEY_PATH.write_text(secret)
            return _derive_key(secret)
        except OSError:
            return _derive_key(os.urandom(32).hex())  # ephemeral fallback

    def key_ref(self) -> str:
        return "file:derive(hkdf-sha256)"

    def wrap_dek(self, dek: bytes) -> tuple[bytes, bytes]:
        nonce = os.urandom(12)
        return AESGCM(self._kek).encrypt(nonce, dek, None), nonce

    def unwrap_dek(self, wrapped: bytes, nonce: bytes) -> bytes:
        try:
            return AESGCM(self._kek).decrypt(nonce, wrapped, None)
        except Exception as exc:
            raise KmsError("file KMS failed to unwrap DEK (key changed?)") from exc


# ---------------------------------------------------------------------------
# cloud backends (lazy SDK imports; configure via env + set backend via
# FLUXSWARM_KMS_BACKEND)
# ---------------------------------------------------------------------------

class _UnconfiguredKms(KmsClient):
    """Placeholder preventing silent misconfiguration (kept import-safe)."""

    def __init__(self, backend: str, what: str):
        self.backend = backend
        self._what = what

    def key_ref(self) -> str:
        return f"{self.backend}:unconfigured"

    def wrap_dek(self, dek: bytes) -> tuple[bytes, bytes]:
        raise KmsError(f"{self.backend} KMS not configured: {self._what}")

    def unwrap_dek(self, wrapped: bytes, nonce: bytes) -> bytes:
        raise KmsError(f"{self.backend} KMS not configured: {self._what}")


def _aws_kms_client() -> KmsClient:
    key_id = _env("FLUXSWARM_AWS_KMS_KEY_ID")
    if not key_id:
        return _UnconfiguredKms("aws_kms", "set FLUXSWARM_AWS_KMS_KEY_ID")
    try:
        import boto3  # type: ignore
    except ImportError as exc:
        return _UnconfiguredKms("aws_kms", f"boto3 not installed: {exc}")

    class _AwsKms(KmsClient):
        backend = "aws_kms"

        def __init__(self, kid: str):
            session = boto3.session.Session()
            self._kms = session.client("kms")
            self._kid = kid

        def key_ref(self) -> str:
            return f"aws_kms:{self._kid}"

        def wrap_dek(self, dek: bytes) -> tuple[bytes, bytes]:
            resp = self._kms.encrypt(KeyId=self._kid, Plaintext=dek)
            return resp["CiphertextBlob"], b""

        def unwrap_dek(self, wrapped: bytes, nonce: bytes) -> bytes:
            try:
                resp = self._kms.decrypt(CiphertextBlob=wrapped)
                return resp["Plaintext"]
            except Exception as exc:
                raise KmsError(f"aws_kms decrypt failed: {exc}") from exc

    return _AwsKms(key_id)


def _azure_keyvault_client() -> KmsClient:
    url = _env("FLUXSWARM_AZURE_KEYVAULT_URL")
    key = _env("FLUXSWARM_AZURE_KEYVAULT_KEY")
    if not url or not key:
        return _UnconfiguredKms(
            "azure_keyvault",
            "set FLUXSWARM_AZURE_KEYVAULT_URL and FLUXSWARM_AZURE_KEYVAULT_KEY",
        )
    try:
        from azure.identity import DefaultAzureCredential  # type: ignore
        from azure.keyvault.keys import KeyClient  # type: ignore
        from azure.keyvault.keys.crypto import CryptographyClient  # type: ignore
        from azure.keyvault.keys.enums import KeyWrapAlgorithm  # type: ignore
    except ImportError as exc:
        return _UnconfiguredKms("azure_keyvault", f"azure SDKs not installed: {exc}")

    class _AzureKms(KmsClient):
        backend = "azure_keyvault"

        def __init__(self):
            self._crypto = CryptographyClient(
                KeyClient(url, DefaultAzureCredential()).get_key(key),
                DefaultAzureCredential(),
            )

        def key_ref(self) -> str:
            return f"azure_keyvault:{key}@{url}"

        def wrap_dek(self, dek: bytes) -> tuple[bytes, bytes]:
            resp = self._crypto.wrap_key(KeyWrapAlgorithm.rsa_oaep, dek)
            return resp.encrypted_key, b""

        def unwrap_dek(self, wrapped: bytes, nonce: bytes) -> bytes:
            try:
                resp = self._crypto.unwrap_key(KeyWrapAlgorithm.rsa_oaep, wrapped)
                return resp.key
            except Exception as exc:
                raise KmsError(f"azure_keyvault unwrap failed: {exc}") from exc

    return _AzureKms()


def _hashicorp_vault_client() -> KmsClient:
    addr = _env("FLUXSWARM_VAULT_ADDR")
    token = _env("FLUXSWARM_VAULT_TOKEN")
    tkey = _env("FLUXSWARM_VAULT_TRANSIT_KEY")
    if not addr or not token or not tkey:
        return _UnconfiguredKms(
            "hashicorp_vault",
            "set FLUXSWARM_VAULT_ADDR, FLUXSWARM_VAULT_TOKEN, FLUXSWARM_VAULT_TRANSIT_KEY",
        )
    try:
        import hvac  # type: ignore
    except ImportError as exc:
        return _UnconfiguredKms("hashicorp_vault", f"hvac not installed: {exc}")

    class _VaultKms(KmsClient):
        backend = "hashicorp_vault"

        def __init__(self):
            self._client = hvac.Client(url=addr, token=token)
            self._key = tkey

        def key_ref(self) -> str:
            return f"hashicorp_vault:{tkey}@{addr}"

        def wrap_dek(self, dek: bytes) -> tuple[bytes, bytes]:
            resp = self._client.secrets.transit.encrypt_data(
                name=self._key, plaintext=b64e(dek),
            )
            return b64d(resp["data"]["ciphertext"]), b""

        def unwrap_dek(self, wrapped: bytes, nonce: bytes) -> bytes:
            try:
                resp = self._client.secrets.transit.decrypt_data(
                    name=self._key, ciphertext=b64e(wrapped),
                )
                return b64d(resp["data"]["plaintext"])
            except Exception as exc:
                raise KmsError(f"hashicorp_vault decrypt failed: {exc}") from exc

    return _VaultKms()


_BACKENDS: dict[str, callable] = {
    "file": FileKms,
    "aws_kms": _aws_kms_client,
    "azure_keyvault": _azure_keyvault_client,
    "hashicorp_vault": _hashicorp_vault_client,
}


def get_client() -> KmsClient:
    """Factory: build the KMS client for FLUXSWARM_KMS_BACKEND (default file)."""
    which = _env("FLUXSWARM_KMS_BACKEND", "file").lower()
    factory = _BACKENDS.get(which)
    if factory is None:
        raise KmsError(
            f"unknown FLUXSWARM_KMS_BACKEND={which!r} "
            f"(choose from {', '.join(sorted(_BACKENDS))})"
        )
    return factory()