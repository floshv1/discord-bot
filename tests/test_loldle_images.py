"""LoLdle images: colours and names only, never a champion."""

import dataclasses
import datetime as dt
import io

from PIL import Image

from bot.cogs.loldle import images
from bot.cogs.loldle.images import PlayerCard

RED, GREEN, YELLOW = ["red"] * 9, ["green"] * 9, ["yellow"] * 9


def _open(png: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG"
    return image.convert("RGB")


def _avatar(colour=(10, 120, 200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(out, "PNG")
    return out.getvalue()


def card(**over) -> PlayerCard:
    base = {"name": "NicoBooy", "avatar": None, "rows": [RED], "tries": 1, "solved": False, "finished": False}
    return PlayerCard(**(base | over))


def test_game_image_is_png_and_grows_with_rows():
    one = _open(images.render_game(card()))
    three = _open(images.render_game(card(rows=[RED, YELLOW, GREEN], tries=3)))
    assert three.height > one.height
    assert three.width == one.width


def test_cell_colours_match_the_palette():
    image = _open(images.render_game(card(rows=[GREEN, RED, YELLOW], tries=3)))
    assert image.getpixel(images.cell_centre(0, 0)) == images.COLOURS["green"]
    assert image.getpixel(images.cell_centre(1, 4)) == images.COLOURS["red"]
    assert image.getpixel(images.cell_centre(2, 8)) == images.COLOURS["yellow"]
    assert images.COLOURS["green"] == (47, 158, 98)
    assert images.COLOURS["red"] == (168, 64, 74)
    assert images.COLOURS["yellow"] == (201, 150, 43)


def test_rows_beyond_max_are_summarised():
    rows = [RED] * 13 + [GREEN]
    full = _open(images.render_game(card(rows=rows, tries=14)))
    capped = _open(images.render_game(card(rows=[RED] * images.MAX_ROWS, tries=images.MAX_ROWS)))
    # Only the last MAX_ROWS rows are drawn, plus one line saying how many are hidden.
    assert full.height - capped.height == images.SUMMARY_HEIGHT
    assert images.hidden_rows(14) == 4
    assert full.getpixel(images.cell_centre(images.MAX_ROWS - 1, 0, summary=True)) == images.COLOURS["green"]


def test_initials_when_no_avatar():
    _open(images.render_game(card(avatar=None)))
    assert images.initials("NicoBooy") == "NI"
    assert images.initials("Feur Stassanne") == "FS"
    assert images.initials("") == "?"


def test_corrupt_avatar_falls_back_to_initials():
    _open(images.render_game(card(avatar=b"not an image")))
    _open(images.render_recap(dt.date(2026, 11, 23), 6, [card(avatar=b"\x89PNG broken")]))


def test_real_avatar_is_drawn():
    with_avatar = _open(images.render_game(card(avatar=_avatar((10, 120, 200)))))
    x, y = images.AVATAR_CENTRE
    assert with_avatar.getpixel((x, y)) == (10, 120, 200)


def test_arrow_cells_are_drawn():
    image = _open(images.render_game(card(rows=[["up"] + ["red"] * 8, ["down"] + ["red"] * 8], tries=2)))
    centre = image.getpixel(images.cell_centre(0, 0))
    assert centre != images.COLOURS["red"]  # the arrow sits on a neutral cell


def test_recap_caps_players_and_crowns():
    cards = [card(name=f"P{i}", tries=i + 1, solved=True, finished=True, crowned=i == 0) for i in range(15)]
    full = _open(images.render_recap(dt.date(2026, 11, 23), 6, cards))
    twelve = _open(images.render_recap(dt.date(2026, 11, 23), 6, cards[: images.MAX_RECAP_PLAYERS]))
    assert images.hidden_players(15) == 3
    assert full.height == twelve.height + images.SUMMARY_HEIGHT
    assert images.CROWN_COLOUR in {full.getpixel(p) for p in images.crown_points(0)}


def test_recap_without_streak_or_players_still_renders():
    _open(images.render_recap(dt.date(2026, 11, 23), 0, []))


def test_inputs_carry_no_champion_field():
    fields = {f.name for f in dataclasses.fields(PlayerCard)}
    assert fields == {"name", "avatar", "rows", "tries", "solved", "finished", "crowned"}


def test_png_has_no_metadata():
    image = Image.open(io.BytesIO(images.render_game(card())))
    assert "icc_profile" not in image.info and "exif" not in image.info


def test_dejavu_is_the_first_font_choice():
    # The bot image installs fonts-dejavu-core: without a real font, accents print as boxes.
    assert images.FONT_CANDIDATES[0] == "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    assert "fonts-dejavu-core" in open("Dockerfile", encoding="utf-8").read()
