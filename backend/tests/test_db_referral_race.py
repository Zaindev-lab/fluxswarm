"""RED/GREEN test for the referral-reward double-credit race in db.py.

The production flow: ``create_user(email, ..., ref_code=X)`` inserts exactly ONE
pending referrals row (rewarded=0). ``reward_referrer_once`` must credit the
referrer at most once per referred email, even if two paid subscriptions race.

Bug under test (now fixed): the reward used ``UPDATE ... WHERE referred_email=?
AND rewarded=0`` wrapped in BEGIN IMMEDIATE. Two concurrent callers can BOTH
SELECT the same pending row before either commits, so both pass and the referrer
is credited twice. The fix claims the specific pending ROW by id with a rowcount
guard, so the loser returns False instead of minting a second reward.
"""
from __future__ import annotations

import db as db_mod
import threading

_TAG = 0


def _seed_referral():
    """Create a referrer + a referred user; create_user inserts the single
    pending referrals row for us (so the test mirrors production exactly)."""
    global _TAG
    _TAG += 1
    tag = _TAG
    ref_email = f"ref_{tag}@x.com"
    buy_email = f"buyer_{tag}@x.com"
    ref = db_mod.create_user(ref_email, "Ref", "pw1")
    db_mod.create_user(buy_email, "Buyer", "pw2", ref_code=ref["ref_code"])
    db_mod.upgrade_plan(2, "starter")
    return ref["id"], buy_email


def test_reward_once_does_not_double_credit():
    ref_id, buy_email = _seed_referral()
    before = db_mod.get_user_by_id(ref_id)["credits"]
    assert db_mod.reward_referrer_once(buy_email) is True
    # No pending row remains -> second call must be a no-op.
    assert db_mod.reward_referrer_once(buy_email) is False
    after = db_mod.get_user_by_id(ref_id)["credits"]
    assert after - before == db_mod.REFERRAL_REWARD_CREDITS


def test_reward_once_concurrent_no_double_credit():
    """Two concurrent reward attempts must result in exactly ONE credit."""
    ref_id, buy_email = _seed_referral()
    before = db_mod.get_user_by_id(ref_id)["credits"]
    results = []

    def attempt():
        results.append(db_mod.reward_referrer_once(buy_email))

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 1, results

    rewarded = db_mod._conn().execute(
        "SELECT COUNT(*) AS n FROM referrals WHERE rewarded=1 AND referred_email=?",
        (buy_email,),
    ).fetchone()["n"]
    assert rewarded == 1

    after = db_mod.get_user_by_id(ref_id)["credits"]
    assert after - before == db_mod.REFERRAL_REWARD_CREDITS
