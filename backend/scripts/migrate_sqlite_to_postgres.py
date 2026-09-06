"""One-shot migration: SQLite (data/users.db) -> PostgreSQL.

Reads every row from the legacy SQLite database and inserts it into the
PostgreSQL target using ``ON CONFLICT DO NOTHING`` so re-runs are idempotent
(no unique-violation explosions, per-row progress logged). The destination
schema must already exist: run ``python -m scripts.migrate_sqlite_to_postgres``
after ``db_postgres.init_db()`` (which applies Alembic head) — see
``docs/MIGRATION_GUIDE.md``.

JSON payload columns (squad_templates.agents, payment_events.detail) are carried
over as-is; PostgreSQL validates/normalizes them, satisfying the CHECK/JSON
constraints on the target.

Usage:
    FLUXSWARM_DATABASE_URL=postgresql://... python -m scripts.migrate_sqlite_to_postgres [sqlite.db]

Exit code 0 on success.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import asyncpg

# backend/ is the cwd baseline; allow running as `python -m scripts....`
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

TABLES = (
    "users",
    "projects",
    "referrals",
    "squad_templates",
    "template_purchases",
    "payment_events",
    "telegram_links",
    "telegram_codes",
    "password_resets",
    "demo_usage",
    "provider_agreements",
)


def connect_sqlite(db_path: str | Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


def row_to_pg(row: sqlite3.Row, table: str) -> dict:
    """Translate a SQLite row to PostgreSQL-native types.

    * Timestamps: SQLite REAL epoch -> PostgreSQL BIGINT (int seconds).
    * JSON text columns: parsed once by Python so asyncpg's json codec round-trips
      them as real JSON on the PG side (never string-odds).
    """
    data = dict(row)
    if table in ("squad_templates",):
        if "agents" in data and isinstance(data["agents"], str):
            try:
                # Validate (and re-serialize) so the text->json cast never fails.
                data["agents"] = json.dumps(json.loads(data["agents"]), ensure_ascii=False)
            except (ValueError, TypeError):
                data["agents"] = "[]"
    if table in ("payment_events",):
        if "detail" in data and isinstance(data["detail"], str):
            try:
                data["detail"] = json.dumps(json.loads(data["detail"]), ensure_ascii=False)
            except (ValueError, TypeError):
                data["detail"] = "{}"
    for col in ("created_at", "linked_at", "expires_at", "logged_out_at", "launch_updated_at"):
        if col in data and isinstance(data[col], float):
            data[col] = int(data[col])
    if table == "projects" and "launch_refunded" in data:
        data["launch_refunded"] = int(data["launch_refunded"] or 0)
    return data


async def migrate_one(pool: asyncpg.Pool, table: str, db_path: str | Path) -> int:
    conn = connect_sqlite(db_path)
    try:
        cols_line = conn.execute(f"SELECT * FROM {table} LIMIT 0").description
        cols = [d[0] for d in cols_line]
        placeholders = ", ".join(f"${i+1}" for i in range(len(cols)))
        insert_sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
            "ON CONFLICT DO NOTHING"
        )
        updated = 0
        for row in conn.execute(f"SELECT * FROM {table}"):
            data = row_to_pg(row, table)
            values = tuple(data.get(c) for c in cols)
            status = await pool.execute(insert_sql, *values)
            if status == "INSERT 0 1":
                updated += 1
        return updated
    finally:
        conn.close()


async def run(db_path: str | Path, url: str) -> None:
    pool = await asyncpg.create_pool(dsn=url, min_size=2, max_size=10, command_timeout=60)
    total = 0
    try:
        for table in TABLES:
            try:
                n = await migrate_one(pool, table, db_path)
                total += n
                print(f"[{time.strftime('%H:%M:%S')}] {table}: +{n} rows")
            except asyncpg.UndefinedTableError:
                print(f"[{time.strftime('%H:%M:%S')}] {table}: SKIP (missing in PG)")
            except Exception as e:  # noqa: BLE001 - per-table isolation + report
                print(f"[{time.strftime('%H:%M:%S')}] {table}: ERROR {e!r} — continuing", file=sys.stderr)
    finally:
        await pool.close()
    print(f"done. total migrated rows: {total}")


def main() -> int:
    url = os.environ.get("FLUXSWARM_DATABASE_URL", "")
    if not url:
        print("FLUXSWARM_DATABASE_URL is required.", file=sys.stderr)
        return 2
    db_path = sys.argv[1] if len(sys.argv) > 1 else str(BACKEND / "data" / "users.db")
    if not Path(db_path).exists():
        print(f"sqlite file not found: {db_path}", file=sys.stderr)
        return 2
    asyncio.run(run(db_path, url))
    return 0


if __name__ == "__main__":
    sys.exit(main())