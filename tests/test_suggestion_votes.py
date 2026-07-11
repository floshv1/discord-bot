import pytest

from bot.cogs.suggestions.cog import SuggestionVoteView, VoteButton


def _custom_ids(view) -> list[str]:
    return [item.custom_id for item in view.children]


def test_vote_buttons_encode_the_suggestion_and_direction():
    ids = _custom_ids(SuggestionVoteView(42, vote_up=3, vote_down=1))
    assert ids == ["suggestion:vote_up:42", "suggestion:vote_down:42"]


@pytest.mark.parametrize(
    ("custom_id", "suggestion_id", "direction"),
    [
        ("suggestion:vote_up:42", 42, 1),
        ("suggestion:vote_down:7", 7, -1),
        ("suggestion:vote_up:1234567890", 1234567890, 1),
    ],
)
def test_template_parses_any_suggestion_id(custom_id, suggestion_id, direction):
    # One registration must serve every suggestion — including ones created after boot,
    # which the old "register a view per existing row" approach could never do.
    match = VoteButton.__discord_ui_compiled_template__.fullmatch(custom_id)
    assert match is not None
    assert int(match["sid"]) == suggestion_id
    assert (1 if match["direction"] == "vote_up" else -1) == direction


def test_template_ignores_the_entry_point_buttons():
    # /setup suggestions posts "suggestion:feature" / "suggestion:improvement" — the vote
    # template must not swallow those.
    for other in ("suggestion:feature", "suggestion:improvement"):
        assert VoteButton.__discord_ui_compiled_template__.fullmatch(other) is None
