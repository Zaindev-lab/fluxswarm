"""
FluxSwarm auth + persistence layer.

Users are stored in a local SQLite DB (data/users.db). Passwords hashed with
Argon2id (argon2-cffi). Legacy sha256+salt hashes from older versions are still
verifiable and are **upgraded in place** on the user's next successful login.
Each user gets an isolated namespace; their FluxSwarm projects map to Hermes
kanban boards prefixed with their user id. Referral codes and plan tiers live
here too.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

try:
    from argon2 import PasswordHasher
    from argon2.exceptions import VerificationError

    _PH = PasswordHasher()
except ImportError:  # pragma: no cover - argon2-cffi is in requirements.txt
    _PH = None

    class VerificationError(Exception):
        pass

BASE = Path(__file__).resolve().parent
# Allow tests / isolated environments to redirect the DB via env (no default change).
DB = BASE / os.environ.get("FLUXSWARM_DB", "data/users.db")
DB.parent.mkdir(exist_ok=True)

# Plan catalogue (monthly). Demo is free and pre-seeded.
PLANS = {
    "demo": {"name": "Demo", "price": 0, "credits": 3, "parallel": 1, "desc": "تجربة مجانية محدودة"},
    "starter": {"name": "Starter", "price": 29, "credits": 25, "parallel": 2, "desc": "للمستقلين والمشاريع الصغيرة"},
    "pro": {"name": "Pro", "price": 99, "credits": 120, "parallel": 4, "desc": "للفرق الصغيرة"},
    "scale": {"name": "Scale", "price": 299, "credits": 500, "parallel": 6, "desc": "للشركات والوكالات"},
}
PLAN_ORDER = ["demo", "starter", "pro", "scale"]

REFERRAL_REWARD_CREDITS = 25  # credits granted to referrer when referred user subscribes (>= starter)


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = _conn()
    c.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            pw_hash TEXT NOT NULL,
            plan TEXT NOT NULL DEFAULT 'demo',
            credits INTEGER NOT NULL DEFAULT 3,
            ref_code TEXT UNIQUE NOT NULL,
            referred_by TEXT,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            board_slug TEXT NOT NULL,
            name TEXT NOT NULL,
            goal TEXT NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_code TEXT NOT NULL,
            referred_email TEXT NOT NULL,
            rewarded INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS squad_templates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            agents TEXT NOT NULL,
            price_credits INTEGER NOT NULL DEFAULT 10,
            created_at REAL NOT NULL,
            FOREIGN KEY(author_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS template_purchases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            template_id INTEGER NOT NULL,
            buyer_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            FOREIGN KEY(template_id) REFERENCES squad_templates(id),
            FOREIGN KEY(buyer_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS payment_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            gateway TEXT NOT NULL,
            kind TEXT NOT NULL,
            user_id INTEGER,
            detail TEXT NOT NULL DEFAULT '{}',
            created_at REAL NOT NULL
        );
        """
    )
    c.commit()
    c.close()


def _hash_legacy(pw: str, salt: str) -> str:
    """Legacy format from pre-Argon2 versions: sha256(salt+pw), salt stored inline."""
    return hashlib.sha256((salt + pw).encode("utf-8")).hexdigest()


def _make_pw_hash(password: str) -> str:
    """Argon2id hash (salt + params embedded in the string)."""
    if _PH:
        return _PH.hash(password)
    # Should never happen in prod (argon2-cffi is required) — belt & suspenders.
    salt = secrets.token_hex(8)
    return f"{salt}${_hash_legacy(password, salt)}"


def _is_legacy(stored: str) -> bool:
    return bool(stored) and not stored.startswith("$argon2") and stored.count("$") == 1


def _verify_password(password: str, stored: str) -> bool:
    if not stored:
        return False
    if stored.startswith("$argon2"):
        if not _PH:
            return False
        try:
            return _PH.verify(stored, password)
        except Exception:
            return False
    if _is_legacy(stored):
        salt, h = stored.split("$", 1)
        return _hash_legacy(password, salt) == h
    return False


def _needs_rehash(stored: str) -> bool:
    if not stored.startswith("$argon2"):
        return True
    try:
        return bool(_PH and _PH.check_needs_rehash(stored))
    except Exception:
        return True


def make_ref_code() -> str:
    return "FLX-" + secrets.token_hex(4).upper()


def create_user(email: str, name: str, password: str, ref_code: str | None = None) -> dict:
    email = email.lower().strip()
    c = _conn()
    try:
        salt = secrets.token_hex(8)
        ref = make_ref_code()
        cur = c.execute(
            "INSERT INTO users (email,name,pw_hash,plan,credits,ref_code,referred_by,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (email, name, _make_pw_hash(password), "demo", PLANS["demo"]["credits"], ref, ref_code, time.time()),
        )
        uid = cur.lastrowid
        c.commit()
        # Record referral relationship if a valid code was supplied.
        if ref_code:
            c.execute(
                "INSERT INTO referrals (referrer_code,referred_email,rewarded,created_at) VALUES (?,?,0,?)",
                (ref_code, email, time.time()),
            )
            c.commit()
        return get_user_by_id(uid)
    except sqlite3.IntegrityError:
        raise ValueError("البريد مسجّل مسبقاً")
    finally:
        c.close()


def authenticate(email: str, password: str) -> dict | None:
    email = email.lower().strip()
    c = _conn()
    row = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not row:
        c.close()
        return None
    if not _verify_password(password, row["pw_hash"]):
        c.close()
        return None
    user = dict(row)
    # Silent upgrade: legacy sha256+salt (or outdated argon2 params) -> fresh Argon2id.
    if _needs_rehash(user["pw_hash"]):
        new_hash = _make_pw_hash(password)
        c.execute("UPDATE users SET pw_hash=? WHERE id=?", (new_hash, user["id"]))
        c.commit()
        user["pw_hash"] = new_hash
    c.close()
    return user


def get_user_by_id(uid: int) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    c.close()
    return dict(row) if row else None


def get_user_by_email(email: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    c.close()
    return dict(row) if row else None


def get_user_by_ref(ref_code: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM users WHERE ref_code=?", (ref_code,)).fetchone()
    c.close()
    return dict(row) if row else None


def add_project(user_id: int, board_slug: str, name: str, goal: str) -> int:
    c = _conn()
    cur = c.execute(
        "INSERT INTO projects (user_id,board_slug,name,goal,created_at) VALUES (?,?,?,?,?)",
        (user_id, board_slug, name, goal, time.time()),
    )
    pid = cur.lastrowid
    c.commit()
    c.close()
    return pid


def list_user_projects(user_id: int) -> list[dict]:
    c = _conn()
    rows = c.execute("SELECT * FROM projects WHERE user_id=? ORDER BY created_at DESC", (user_id,)).fetchall()
    c.close()
    return [dict(r) for r in rows]


def deduct_credit(user_id: int) -> bool:
    """Atomically spend one credit. Uses BEGIN IMMEDIATE so two concurrent
    launches cannot both pass the balance check (race-free debit)."""
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT credits FROM users WHERE id=?", (user_id,)).fetchone()
        if not row or row["credits"] <= 0:
            c.rollback()
            return False
        c.execute("UPDATE users SET credits = credits - 1 WHERE id=?", (user_id,))
        c.commit()
        return True
    finally:
        c.close()


def reward_referrer_once(referred_email: str) -> bool:
    """Reward the referrer exactly once per referred email (first paid
    subscription that reaches the billing gate). Prevents credit farming by
    oscillating subscriptions AND prevents double-credit under concurrency.

    The read-modify-write runs inside BEGIN IMMEDIATE so only one caller can
    hold the pending row at a time; the claim UPDATE targets the specific row id
    and relies on its rowcount, so a concurrent loser (whose SELECT returned the
    same pending row before we committed) gets rowcount==0 and returns False
    instead of minting a second reward. This closes the double-credit race that a
    plain ``BEGIN IMMEDIATE`` around a ``WHERE referred_email=? AND rewarded=0``
    UPDATE does NOT fix.
    """
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT id, referrer_code FROM referrals "
            "WHERE referred_email=? AND rewarded=0 ORDER BY id LIMIT 1",
            (referred_email,),
        ).fetchone()
        if not row:
            c.rollback()
            return False
        ref_user = c.execute("SELECT id FROM users WHERE ref_code=?", (row["referrer_code"],)).fetchone()
        if ref_user:
            c.execute("UPDATE users SET credits = credits + ? WHERE id=?",
                      (REFERRAL_REWARD_CREDITS, ref_user["id"]))
        # Claim THIS specific row; only the winner (rowcount==1) commits.
        if c.execute("UPDATE referrals SET rewarded=1 WHERE id=? AND rewarded=0",
                     (row["id"],)).rowcount == 0:
            c.rollback()
            return False
        c.commit()
        return bool(ref_user)
    except Exception:
        c.rollback()
        return False
    finally:
        c.close()


def upgrade_plan(user_id: int, plan: str):
    if plan not in PLANS:
        raise ValueError("باقة غير صالحة")
    # Grant the plan's credit allowance without ever clawing back credits the
    # user already holds, and without refilling on repeated subscriptions to the
    # same tier (closes the infinite-credit-refill exploit once billing is live).
    c = _conn()
    try:
        cur = c.execute("SELECT credits FROM users WHERE id=?", (user_id,)).fetchone()
        current = cur["credits"] if cur else 0
        new_credits = max(current, PLANS[plan]["credits"])
        c.execute("UPDATE users SET plan=?, credits=? WHERE id=?", (plan, new_credits, user_id))
        c.commit()
    finally:
        c.close()


def downgrade_subscription(user_id: int):
    """Drop a user to the free plan (used on a verified payment refund).

    Credits already held are kept — we never claw back pre-paid credits, we only
    prevent further top-ups after the subscription is revoked. The stored value
    is the plan *key* (``demo``), never the display name, so downstream
    ``PLANS[plan]`` lookups keep working after a refund.
    """
    c = _conn()
    try:
        c.execute("UPDATE users SET plan=? WHERE id=? AND plan != ?",
                  ("demo", user_id, "demo"))
        c.commit()
    finally:
        c.close()


def record_payment_event(event_id: str, gateway: str, kind: str, user_id: int, detail: dict) -> bool:
    """Persist a processed payment event; returns False when already seen
    (UNIQUE constraint) so webhook replay is idempotent."""
    import json
    c = _conn()
    try:
        cur = c.execute(
            "INSERT OR IGNORE INTO payment_events (event_id,gateway,kind,user_id,detail,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (event_id, gateway, kind, user_id, json.dumps(detail, ensure_ascii=False), time.time()),
        )
        c.commit()
        return cur.rowcount > 0
    finally:
        c.close()


def payment_user_by_txn(txn_id: str) -> int | None:
    """Map a Paddle transaction id back to the user who paid for it.

    Paddle v1 delivers refunds as ``adjustment.created`` events whose payload
    carries only the original transaction id (no ``custom_data``), so a refund
    cannot name its user directly. We store the txn id on each granted payment
    event (``detail.txn``); the adjustment handler looks it up here to decide
    which subscription to downgrade.
    """
    if not txn_id:
        return None
    import json
    c = _conn()
    try:
        # JSON1 is not guaranteed at runtime; scan explicitly to stay robust.
        found = None
        for r in c.execute(
            "SELECT id, user_id, detail FROM payment_events "
            "WHERE kind='payment.succeeded' ORDER BY id DESC"
        ):
            try:
                detail = json.loads(r["detail"] or "{}")
            except (ValueError, TypeError):
                continue
            if isinstance(detail, dict) and detail.get("txn") == txn_id:
                found = r["user_id"]
                break
        return found
    finally:
        c.close()


# ---------- CCPA / account rights ----------
def account_payload(user_id: int) -> dict:
    """Everything the platform holds about a user (CCPA/CPRA right to access)."""
    import json as _json
    c = _conn()
    try:
        u = c.execute("SELECT id,email,name,plan,credits,ref_code,referred_by,created_at "
                      "FROM users WHERE id=?", (user_id,)).fetchone()
        if not u:
            raise ValueError("user_not_found")
        projects = [dict(r) for r in c.execute(
            "SELECT id,board_slug,name,goal,created_at FROM projects WHERE user_id=?", (user_id,))]
        refs = [dict(r) for r in c.execute(
            "SELECT referrer_code,referred_email,rewarded,created_at FROM referrals WHERE referrer_code=?",
            (u["ref_code"],))]
        tpls = [dict(r) for r in c.execute(
            "SELECT id,name,price_credits,created_at FROM squad_templates WHERE author_id=?", (user_id,))]
        buys = [dict(r) for r in c.execute(
            "SELECT template_id,created_at FROM template_purchases WHERE buyer_id=?", (user_id,))]
        pays = [dict(r) for r in c.execute(
            "SELECT id,gateway,kind,created_at FROM payment_events WHERE user_id=?", (user_id,))]
        return {"user": dict(u), "projects": projects, "referrals": refs,
                "templates": tpls, "template_purchases": buys, "payment_events": pays}
    finally:
        c.close()


def delete_user(user_id: int) -> bool:
    """Permanently erase a user and all their rows (CCPA/CPRA right to delete).
    Returns True if a user row was removed."""
    c = _conn()
    try:
        u = c.execute("SELECT email,ref_code FROM users WHERE id=?", (user_id,)).fetchone()
        if not u:
            return False
        for sql in (
            "DELETE FROM projects WHERE user_id=?",
            "DELETE FROM squad_templates WHERE author_id=?",
            "DELETE FROM template_purchases WHERE buyer_id=?",
            "DELETE FROM referrals WHERE referrer_code=?",
            "DELETE FROM payment_events WHERE user_id=?",
            "DELETE FROM users WHERE id=?",
        ):
            try:
                c.execute(sql, (user_id,))
            except sqlite3.OperationalError:
                pass
        c.commit()
        return True
    finally:
        c.close()


# ---------- squad marketplace ----------
def publish_template(author_id: int, name: str, description: str, agents: list[str], price_credits: int = 10) -> int:
    import json
    c = _conn()
    cur = c.execute(
        "INSERT INTO squad_templates (author_id,name,description,agents,price_credits,created_at) VALUES (?,?,?,?,?,?)",
        (author_id, name, description, json.dumps(agents), price_credits, time.time()),
    )
    tid = cur.lastrowid
    c.commit()
    c.close()
    return tid


def list_templates(author_id: int | None = None) -> list[dict]:
    c = _conn()
    if author_id:
        rows = c.execute("SELECT * FROM squad_templates WHERE author_id=? ORDER BY created_at DESC", (author_id,)).fetchall()
    else:
        rows = c.execute("SELECT * FROM squad_templates ORDER BY created_at DESC").fetchall()
    c.close()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["agents"] = json.loads(d["agents"])
        except Exception:
            d["agents"] = []
        out.append(d)
    return out


def get_template(tid: int) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM squad_templates WHERE id=?", (tid,)).fetchone()
    c.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["agents"] = json.loads(d["agents"])
    except Exception:
        d["agents"] = []
    return d


def buy_template(tid: int, buyer_id: int) -> bool:
    """Atomic purchase: buyer spends price_credits; author earns half the price.

    Wrapped in BEGIN IMMEDIATE so two concurrent purchases cannot both pass the
    balance check (race-free credit debit). Returns False if insufficient funds
    or the template does not exist.
    """
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT * FROM squad_templates WHERE id=?", (tid,)).fetchone()
        if not row:
            c.rollback()
            return False
        price = row["price_credits"]
        bal = c.execute("SELECT credits FROM users WHERE id=?", (buyer_id,)).fetchone()
        if not bal or bal["credits"] < price:
            c.rollback()
            return False
        c.execute("UPDATE users SET credits = credits - ? WHERE id=?", (price, buyer_id))
        # Author earns 50% of price (rounded, min 1).
        author_earn = max(1, price // 2)
        c.execute("UPDATE users SET credits = credits + ? WHERE id=?", (author_earn, row["author_id"]))
        c.execute("INSERT INTO template_purchases (template_id,buyer_id,created_at) VALUES (?,?,?)",
                  (tid, buyer_id, time.time()))
        c.commit()
        return True
    except Exception:
        c.rollback()
        return False
    finally:
        c.close()


def refund_template_purchase(tid: int, buyer_id: int) -> bool:
    """Undo a buy_template charge when the squad launch failed after the debit.

    Runs inside BEGIN IMMEDIATE so it cannot double-refund: we only reverse the
    most recent, still-unrefunded purchase of (tid, buyer_id). The author's
    earned share is clawed back too so no credits are minted on a failed run.
    Returns True if a purchase was reversed.
    """
    c = _conn()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT * FROM template_purchases WHERE template_id=? AND buyer_id=? "
            "ORDER BY id DESC LIMIT 1",
            (tid, buyer_id),
        ).fetchone()
        if not row:
            c.rollback()
            return False
        tpl = c.execute("SELECT price_credits, author_id FROM squad_templates WHERE id=?", (tid,)).fetchone()
        if not tpl:
            c.rollback()
            return False
        price = tpl["price_credits"]
        refund = max(0, price)
        c.execute("UPDATE users SET credits = credits + ? WHERE id=?", (refund, buyer_id))
        # Claw back the author's 50% share so totals stay consistent.
        author_earn = max(1, price // 2)
        c.execute(
            "UPDATE users SET credits = credits - ? WHERE id=(SELECT author_id FROM squad_templates WHERE id=?)",
            (author_earn, tid),
        )
        c.execute("DELETE FROM template_purchases WHERE id=?", (row["id"],))
        c.commit()
        return True
    except Exception:
        c.rollback()
        return False
    finally:
        c.close()


def get_user_credits(user_id: int) -> int:
    c = _conn()
    try:
        row = c.execute("SELECT credits FROM users WHERE id=?", (user_id,)).fetchone()
        return int(row["credits"]) if row else 0
    finally:
        c.close()


# Pre-seed a demo account so visitors can try instantly.
def seed_demo():
    c = _conn()
    if not c.execute("SELECT 1 FROM users WHERE email=?", ("demo@fluxswarm.ai",)).fetchone():
        create_user("demo@fluxswarm.ai", "Demo User", "demo1234")
    c.close()


init_db()
seed_demo()
