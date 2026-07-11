from bot.cogs.betting.views import BUTTON_LABEL_LIMIT, MODAL_TITLE_LIMIT, MarketView, StakeModal, _truncate


def test_truncate_leaves_short_text_alone():
    assert _truncate("France", 45) == "France"


def test_truncate_shortens_overlong_text():
    result = _truncate("x" * 100, 45)
    assert len(result) == 45
    assert result.endswith("…")


def test_stake_modal_title_stays_within_discord_limit():
    # A long custom-bet option would otherwise blow past Discord's 45-char modal title cap.
    modal = StakeModal(1, "home", "A very very very long outcome name indeed")
    assert len(modal.title) <= MODAL_TITLE_LIMIT


def test_stake_modal_keeps_label_for_confirmation():
    modal = StakeModal(1, "home", "France")
    assert modal.outcome == "home"
    assert modal.outcome_label == "France"


def test_button_labels_stay_within_discord_limit():
    view = MarketView(1, [("home", "y" * 200), ("away", "Fnatic")])
    for item in view.children:
        assert len(item.label) <= BUTTON_LABEL_LIMIT


def test_market_view_builds_one_button_per_outcome():
    view = MarketView(7, [("home", "G2"), ("away", "Fnatic")])
    assert [item.custom_id for item in view.children] == ["betting:bet:7:home", "betting:bet:7:away"]
