from bot.cogs.betting.cog import PollStats


def test_new_markets_are_reported_as_posted():
    summary = PollStats(created=1).summary()
    assert "new match" in summary
    assert "1" in summary


def test_already_posted_is_not_reported_as_a_failure():
    # The football feed returning 0 *new* markets means everything is already up, not broken.
    summary = PollStats(created=0, existing=3).summary()
    assert "up to date" in summary
    assert "3" in summary


def test_empty_feed_is_distinguished_from_a_failure():
    assert PollStats().summary() == "no upcoming fixtures found"


def test_failed_provider_is_called_out():
    assert "failed" in PollStats(failed=True).summary()


def test_restored_cards_are_reported():
    # A deleted card leaves the market orphaned: still 'existing', but unbettable until re-posted.
    summary = PollStats(created=0, existing=0, restored=3).summary()
    assert "re-posted" in summary
    assert "3" in summary


def test_restored_and_new_are_reported_together():
    summary = PollStats(created=2, existing=1, restored=3).summary()
    assert "2" in summary and "new" in summary
    assert "3" in summary and "re-posted" in summary
    assert "1 already up" in summary
