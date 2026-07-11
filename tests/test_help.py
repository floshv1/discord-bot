from bot.cogs.help.embeds import build_help_embed


def _text(embed) -> str:
    return "\n".join(f"{f.name}\n{f.value}" for f in embed.fields)


def test_unconfigured_features_are_not_advertised():
    embed = build_help_embed(channels={"currency": None, "betting": None}, is_mod=False)
    text = _text(embed)
    assert "/claim" not in text
    assert "/bet" not in text


def test_configured_feature_lists_its_commands_and_channel():
    embed = build_help_embed(channels={"currency": "#coins"}, is_mod=False)
    text = _text(embed)
    assert "/claim" in text
    assert "/balance" in text
    assert "#coins" in text


def test_betting_surfaces_bet_mine():
    # /bet mine was effectively invisible — nothing in the server named it.
    embed = build_help_embed(channels={"betting": "#bets"}, is_mod=False)
    assert "/bet mine" in _text(embed)


def test_members_do_not_see_the_moderation_section():
    embed = build_help_embed(channels={}, is_mod=False)
    assert "/warn" not in _text(embed)


def test_mods_see_the_moderation_section():
    embed = build_help_embed(channels={}, is_mod=True)
    text = _text(embed)
    assert "/warn" in text
    assert "/setup status" in text


def test_always_available_features_show_without_configuration():
    embed = build_help_embed(channels={}, is_mod=False)
    text = _text(embed)
    assert "/birthday set" in text
    assert "/play" in text
