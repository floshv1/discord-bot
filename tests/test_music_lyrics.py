from bot.cogs.music.utils import LYRICS_TIMEOUT, clean_for_lyrics, pick_lyrics


def test_timeout_is_generous_enough_for_lrclib():
    # lrclib routinely takes ~6s to answer. The old 5s budget timed out on essentially
    # every request, which is why lyrics "didn't work" — nothing to do with the track.
    assert LYRICS_TIMEOUT >= 10


def test_picks_the_first_result_that_actually_has_lyrics():
    results = [
        {"trackName": "Song (Instrumental)", "plainLyrics": ""},
        {"trackName": "Song", "plainLyrics": "real lyrics here"},
    ]
    assert pick_lyrics(results) == "real lyrics here"


def test_does_not_give_up_because_the_top_hit_is_instrumental():
    results = [{"plainLyrics": None}, {"plainLyrics": "  found  "}]
    assert pick_lyrics(results) == "found"


def test_returns_none_when_nothing_has_lyrics():
    assert pick_lyrics([{"plainLyrics": ""}, {"plainLyrics": None}]) is None
    assert pick_lyrics([]) is None


def test_strips_topic_suffix_from_autoplay_artists():
    # YouTube Music (what autoplay recommends from) credits tracks to "Artist - Topic".
    _, artist = clean_for_lyrics("Blinding Lights", "The Weeknd - Topic")
    assert artist == "The Weeknd"
