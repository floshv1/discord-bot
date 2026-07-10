import datetime

import pytest

from bot.cogs.moderation.cog import _parse_until


def test_parse_until_valid_format_returns_utc_datetime():
    result = _parse_until("15/07/2026 20:00")
    assert result.tzinfo == datetime.UTC
    # 20:00 Europe/Paris in July (CEST, UTC+2) is 18:00 UTC.
    assert result == datetime.datetime(2026, 7, 15, 18, 0, tzinfo=datetime.UTC)


def test_parse_until_rejects_wrong_separators():
    with pytest.raises(ValueError, match="JJ/MM/AAAA HH:MM"):
        _parse_until("2026-07-15 20:00")


def test_parse_until_rejects_garbage():
    with pytest.raises(ValueError, match="JJ/MM/AAAA HH:MM"):
        _parse_until("pas une date")


def test_parse_until_rejects_out_of_range_hour():
    with pytest.raises(ValueError, match="JJ/MM/AAAA HH:MM"):
        _parse_until("15/07/2026 25:00")
