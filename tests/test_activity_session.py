from activity.server.limits import RateLimiter
from activity.server.session import issue, issue_ticket, verify, verify_ticket


def test_session_round_trip():
    assert verify("s", issue("s", 7, 100), 200) == 7


def test_expired_token_is_refused():
    assert verify("s", issue("s", 7, 100), 3701) is None


def test_token_valid_until_expiry():
    assert verify("s", issue("s", 7, 100), 3699) == 7


def test_forged_token_is_refused():
    token = issue("s", 7, 100)
    assert verify("other", token, 200) is None
    payload, sig = token.split(".")
    forged = issue("s", 8, 100).split(".")[0] + "." + sig
    assert verify("s", forged, 200) is None
    assert verify("s", payload + "." + sig[:-2] + "AA", 200) is None


def test_malformed_tokens_return_none():
    for bad in ["", "x", "a.b", "a.b.c", "....", "é.é", "!!!.???", "." * 5]:
        assert verify("s", bad, 100) is None
        assert verify_ticket("s", bad, "ws", 100) is None


def test_ticket_is_bound_to_its_resource():
    t = issue_ticket("s", 7, "ws", 100)
    assert verify_ticket("s", t, "ws", 110) == 7
    assert verify_ticket("s", t, "splash", 110) is None


def test_ticket_expires_after_60s():
    t = issue_ticket("s", 7, "ws", 100)
    assert verify_ticket("s", t, "ws", 159) == 7
    assert verify_ticket("s", t, "ws", 161) is None


def test_session_token_is_not_a_ticket():
    assert verify_ticket("s", issue("s", 7, 100), "ws", 110) is None


def test_ticket_is_not_a_session_token():
    assert verify("s", issue_ticket("s", 7, "ws", 100), 110) is None


def test_rate_limiter_window():
    rl = RateLimiter(2, 10)
    assert rl.allow("a", 0) and rl.allow("a", 1)
    assert not rl.allow("a", 2)
    assert rl.allow("b", 2)
    assert rl.allow("a", 11)


def test_rate_limiter_does_not_grow_without_bound():
    rl = RateLimiter(1, 60)
    for i in range(50_000):
        rl.allow(f"k{i}", 0)
    assert len(rl._hits) <= rl.max_keys
