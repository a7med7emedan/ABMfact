from __future__ import annotations

from abmfact.diagnose import RULES, diagnose


def test_specific_rule_wins_when_alone():
    (f,) = diagnose(["update_order"])
    assert "Stale" in f["cause"]


def test_fallback_rule_when_agent_order_also_flagged():
    found = {f["check"]: f for f in diagnose(["agent_order", "update_order"])}
    assert "labels" in found["update_order"]["cause"]
    assert set(found) == {"agent_order", "update_order"}


def test_every_check_has_a_rule_and_order_is_stable():
    checks = list(dict.fromkeys(r.check for r in RULES))
    out = diagnose(reversed(checks))
    assert [f["check"] for f in out] == checks
    assert diagnose([]) == []
