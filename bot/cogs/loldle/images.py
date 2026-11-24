"""LoLdle pictures for the channel: one member's game, and the midnight recap.

Pure Pillow, no Discord and no database, so every layout rule can be tested by pixel. The inputs are
names, avatars and colours only: `PlayerCard` has no champion field, which is what keeps the day's
answer out of every picture the channel sees.
"""

from __future__ import annotations

import datetime as dt
import io
import math
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFont

COLOURS = {
    "green": (47, 158, 98),
    "yellow": (201, 150, 43),
    "red": (168, 64, 74),
    "up": (58, 61, 70),
    "down": (58, 61, 70),
    "none": (44, 46, 53),
}
BACKGROUND = (23, 24, 29)
TEXT = (240, 238, 232)
MUTED = (163, 165, 173)
CROWN_COLOUR = (216, 180, 90)
AVATAR_FILL = (58, 61, 70)

MAX_ROWS = 10
MAX_RECAP_PLAYERS = 12

# --- one member's game ------------------------------------------------------
PAD = 16
CELL = 32
GAP = 4
COLUMNS = 9
GRID_WIDTH = COLUMNS * CELL + (COLUMNS - 1) * GAP
GAME_WIDTH = GRID_WIDTH + 2 * PAD
HEADER_HEIGHT = 60
SUMMARY_HEIGHT = 22
AVATAR = 40
AVATAR_CENTRE = (PAD + AVATAR // 2, 10 + AVATAR // 2)

# --- recap ------------------------------------------------------------------
PER_LINE = 6
COLUMN_WIDTH = 120
MINI = 10
MINI_GAP = 2
MINI_WIDTH = COLUMNS * MINI + (COLUMNS - 1) * MINI_GAP
RECAP_WIDTH = PER_LINE * COLUMN_WIDTH + 2 * PAD
RECAP_HEADER = 44
CROWN_BAND = 18
RECAP_AVATAR = 28
NAME_BAND = 18
LABEL_BAND = 22


@dataclass(frozen=True)
class PlayerCard:
    name: str
    avatar: bytes | None
    rows: list[list[str]]
    tries: int
    solved: bool
    finished: bool
    crowned: bool = False


# Pillow's bundled font has no accented glyphs ("Trouvé" would print a box), so a real font comes
# first: DejaVu is installed in the bot image (Dockerfile), the others cover local runs.
FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "DejaVuSans.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
)


@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def initials(name: str) -> str:
    words = [w for w in name.replace("_", " ").replace("-", " ").split() if w]
    if not words:
        return "?"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def hidden_rows(tries: int) -> int:
    return max(0, tries - MAX_ROWS)


def hidden_players(count: int) -> int:
    return max(0, count - MAX_RECAP_PLAYERS)


def _plural(n: int) -> str:
    return "essai" if n == 1 else "essais"


def _status(card: PlayerCard) -> str:
    if card.solved:
        return f"Trouvé en {card.tries} {_plural(card.tries)}"
    if card.finished:
        return f"Pas trouvé · {card.tries} {_plural(card.tries)}"
    return f"En cours · {card.tries} {_plural(card.tries)}"


def _avatar_image(avatar: bytes | None, size: int, name: str) -> Image.Image:
    """A round avatar, or the member's initials on a neutral disc when there is none or it is broken."""
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    face: Image.Image | None = None
    if avatar:
        try:
            with Image.open(io.BytesIO(avatar)) as raw:
                face = raw.convert("RGB").resize((size, size), Image.Resampling.LANCZOS)
        except (OSError, ValueError, Image.DecompressionBombError):
            face = None
    if face is None:
        face = Image.new("RGB", (size, size), AVATAR_FILL)
        draw = ImageDraw.Draw(face)
        draw.text((size / 2, size / 2), initials(name), fill=CROWN_COLOUR, font=_font(size // 2 - 2), anchor="mm")
    disc = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    disc.paste(face, (0, 0), mask)
    return disc


def _cell(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], state: str) -> None:
    draw.rounded_rectangle(box, radius=max(2, (box[2] - box[0]) // 6), fill=COLOURS.get(state, COLOURS["none"]))
    if state not in ("up", "down"):
        return
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    half = (x1 - x0) * 0.28
    if state == "up":
        points = [(cx, cy - half), (cx - half, cy + half * 0.8), (cx + half, cy + half * 0.8)]
    else:
        points = [(cx, cy + half), (cx - half, cy - half * 0.8), (cx + half, cy - half * 0.8)]
    draw.polygon(points, fill=TEXT)


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    rgb = image.convert("RGB")
    rgb.info = {}
    rgb.save(out, "PNG", optimize=True)
    return out.getvalue()


def cell_centre(row: int, column: int, *, summary: bool = False) -> tuple[int, int]:
    """Centre of a drawn cell in `render_game` (row 0 is the oldest drawn guess)."""
    top = HEADER_HEIGHT + (SUMMARY_HEIGHT if summary else 0)
    return (
        PAD + column * (CELL + GAP) + CELL // 2,
        top + row * (CELL + GAP) + CELL // 2,
    )


def render_game(card: PlayerCard) -> bytes:
    """One member's day so far: avatar, name, status, then one row of colours per guess."""
    hidden = hidden_rows(len(card.rows))
    rows = card.rows[-MAX_ROWS:]
    summary = SUMMARY_HEIGHT if hidden else 0
    grid = len(rows) * (CELL + GAP) - GAP if rows else 0
    height = HEADER_HEIGHT + summary + grid + PAD
    image = Image.new("RGB", (GAME_WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(image)

    face = _avatar_image(card.avatar, AVATAR, card.name)
    image.paste(face, (PAD, 10), face)
    draw.text((PAD + AVATAR + 12, 12), card.name[:24], fill=TEXT, font=_font(17))
    draw.text((PAD + AVATAR + 12, 34), _status(card), fill=CROWN_COLOUR if card.solved else MUTED, font=_font(13))
    if hidden:
        draw.text((PAD, HEADER_HEIGHT), f"+{hidden} {_plural(hidden)} plus tôt", fill=MUTED, font=_font(12))
    for r, cells in enumerate(rows):
        for c, state in enumerate(cells[:COLUMNS]):
            x, y = cell_centre(r, c, summary=bool(hidden))
            _cell(draw, (x - CELL // 2, y - CELL // 2, x + CELL // 2 - 1, y + CELL // 2 - 1), state)
    return _png(image)


def _block_height(cards: list[PlayerCard]) -> int:
    longest = max((min(len(c.rows), MAX_ROWS) for c in cards), default=0)
    grid = longest * (MINI + MINI_GAP) - MINI_GAP if longest else 0
    return CROWN_BAND + RECAP_AVATAR + NAME_BAND + grid + LABEL_BAND


def _column_origin(index: int, block: int) -> tuple[int, int]:
    line, slot = divmod(index, PER_LINE)
    return PAD + slot * COLUMN_WIDTH, RECAP_HEADER + line * block


def crown_points(index: int, cards: list[PlayerCard] | None = None) -> list[tuple[int, int]]:
    """A few points inside the crown drawn above player `index` (first line of the recap)."""
    x, y = _column_origin(index, _block_height(cards or []))
    cx = x + COLUMN_WIDTH // 2
    base = y + CROWN_BAND - 5
    return [(cx + dx, base) for dx in (-4, -2, 0, 2, 4)]


def _crown(draw: ImageDraw.ImageDraw, cx: int, top: int) -> None:
    w, h = 22, 12
    left, right, bottom = cx - w // 2, cx + w // 2, top + h
    draw.polygon(
        [
            (left, bottom),
            (left, top + 3),
            (cx - w // 4, top + 7),
            (cx, top),
            (cx + w // 4, top + 7),
            (right, top + 3),
            (right, bottom),
        ],
        fill=CROWN_COLOUR,
    )


def render_recap(day: dt.date, streak: int, cards: list[PlayerCard]) -> bytes:
    """Yesterday at a glance: every member's grid side by side, the best one crowned."""
    shown = cards[:MAX_RECAP_PLAYERS]
    hidden = hidden_players(len(cards))
    block = _block_height(shown)
    lines = math.ceil(len(shown) / PER_LINE)
    height = RECAP_HEADER + lines * block + (SUMMARY_HEIGHT if hidden else 0) + PAD
    image = Image.new("RGB", (RECAP_WIDTH, height), BACKGROUND)
    draw = ImageDraw.Draw(image)

    draw.text((PAD, 14), f"LoLdle du {day:%d/%m}", fill=CROWN_COLOUR, font=_font(18))
    if streak >= 2:
        draw.text((RECAP_WIDTH - PAD, 16), f"Série : {streak} jours", fill=MUTED, font=_font(14), anchor="ra")

    for i, player in enumerate(shown):
        x, y = _column_origin(i, block)
        cx = x + COLUMN_WIDTH // 2
        if player.crowned:
            _crown(draw, cx, y + 2)
        face = _avatar_image(player.avatar, RECAP_AVATAR, player.name)
        image.paste(face, (cx - RECAP_AVATAR // 2, y + CROWN_BAND), face)
        draw.text((cx, y + CROWN_BAND + RECAP_AVATAR + 3), player.name[:14], fill=TEXT, font=_font(12), anchor="ma")
        top = y + CROWN_BAND + RECAP_AVATAR + NAME_BAND
        left = cx - MINI_WIDTH // 2
        rows = player.rows[-MAX_ROWS:]
        for r, cells in enumerate(rows):
            for c, state in enumerate(cells[:COLUMNS]):
                x0 = left + c * (MINI + MINI_GAP)
                y0 = top + r * (MINI + MINI_GAP)
                _cell(draw, (x0, y0, x0 + MINI - 1, y0 + MINI - 1), state)
        label = str(player.tries) if player.solved else "X"
        grid = len(rows) * (MINI + MINI_GAP) - MINI_GAP if rows else 0
        draw.text(
            (cx, top + grid + 4),
            label,
            fill=CROWN_COLOUR if player.crowned else MUTED,
            font=_font(13),
            anchor="ma",
        )
    if hidden:
        draw.text(
            (PAD, RECAP_HEADER + lines * block),
            f"+{hidden} autre{'s' if hidden > 1 else ''}",
            fill=MUTED,
            font=_font(13),
        )
    return _png(image)
