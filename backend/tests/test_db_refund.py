"""RED/GREEN tests for db.refund_template_purchase.

Bug under test: when a marketplace template launch fails AFTER the credit debit,
the buyer was charged with no refund. The fix reverses the purchase (buyer gets
the price back, author's 50% share is clawed back) and never double-refunds.
"""
from __future__ import annotations

import db as db_mod


def _seed_purchase():
    tag = int(__import__("time").time() * 1000) % 1000000
    author = db_mod.create_user(f"author_{tag}@x.com", "Author", "pw1")
    buyer = db_mod.create_user(f"buyer_{tag}@x.com", "Buyer", "pw2")
    # Give the buyer enough credits to afford a 10-credit template.
    db_mod.upgrade_plan(buyer["id"], "pro")
    tid = db_mod.publish_template(author["id"], "Tpl", "desc", ["Planner"], price_credits=10)
    ok = db_mod.buy_template(tid, buyer["id"])
    assert ok is True
    return tid, author["id"], buyer["id"]


def test_refund_reverses_buyer_and_author():
    tid, author_id, buyer_id = _seed_purchase()
    before_buyer = db_mod.get_user_credits(buyer_id)
    before_author = db_mod.get_user_credits(author_id)
    ok = db_mod.refund_template_purchase(tid, buyer_id)
    assert ok is True
    # Buyer gets the full price (10) back.
    assert db_mod.get_user_credits(buyer_id) - before_buyer == 10
    # Author's 50% share (5) is clawed back.
    assert before_author - db_mod.get_user_credits(author_id) == 5


def test_refund_idempotent():
    tid, author_id, buyer_id = _seed_purchase()
    assert db_mod.refund_template_purchase(tid, buyer_id) is True
    # Second refund finds no open purchase -> returns False, no extra credit.
    credits_after_first = db_mod.get_user_credits(buyer_id)
    assert db_mod.refund_template_purchase(tid, buyer_id) is False
    assert db_mod.get_user_credits(buyer_id) == credits_after_first
