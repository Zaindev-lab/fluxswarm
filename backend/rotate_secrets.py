"""Rotate FluxSwarm secrets safely and snapshot a recovery point.

Recovery point: ~/.fluxswarm/backups/<ts>/ contains
  - previous .jwt_secret and fernet.key (the pre-rotation values),
  - a copy of users.db and byok.json (consistent-ish data snapshot).

Then it writes fresh values:
  - backend/data/.jwt_secret  (48-byte urlsafe token)  -> invalidates old sessions
  - ~/.fluxswarm/fernet.key   (new Fernet key)          -> decryption of NEW blobs

Required safety guard: rotating the Fernet key makes ANY existing ciphertext
undecryptable. That is only safe while the vault is EMPTY ({}). The script
refuses to continue otherwise.
"""
from __future__ import annotations

import json
import secrets
import shutil
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
VAULT_FILE = DATA / "byok.json"
JWT_FILE = DATA / ".jwt_secret"
FERNET_DIR = Path.home() / ".fluxswarm"
FERNET_FILE = FERNET_DIR / "fernet.key"


def main() -> int:
    vault = {}
    if VAULT_FILE.exists():
        try:
            vault = json.loads(VAULT_FILE.read_text(encoding="utf-8"))
        except Exception:
            print("! byok.json is corrupt — refusing to rotate.", file=sys.stderr)
            return 2
    if vault:
        print("! Vault is NOT empty — rotating the Fernet key would destroy", file=sys.stderr)
        print("  stored BYOK keys. Clearing/decrypting is the operator's call.", file=sys.stderr)
        return 2

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = FERNET_DIR / "backups" / stamp
    backup.mkdir(parents=True, exist_ok=True)

    saved = []
    for src in (JWT_FILE, FERNET_FILE, VAULT_FILE, DATA / "users.db"):
        if src and src.exists():
            shutil.copy2(src, backup / src.name)
            saved.append(str(backup / src.name))
    print("Recovery point:", *saved, sep="\n  ")

    JWT_FILE.parent.mkdir(parents=True, exist_ok=True)
    JWT_FILE.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    FERNET_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from cryptography.fernet import Fernet
        FERNET_FILE.write_text(Fernet.generate_key().decode(), encoding="utf-8")
    except ImportError:
        print("! cryptography missing — fernet key NOT rotated.", file=sys.stderr)
        return 3

    print("Rotated .jwt_secret and fernet.key.")
    print("Restart the backend so the new secrets are loaded (old sessions log out).")
    return 0


if __name__ == "__main__":
    sys.exit(main())