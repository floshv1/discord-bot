"""Paid hints: never spoil through a URL or filename, never charge twice, never charge for nothing."""

import datetime as dt
import io
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from PIL import Image

from bot.cogs.currency.embeds import REASON_LABELS
from bot.cogs.currency.service import HOUSE_USER_ID
from bot.cogs.loldle import catalog, hints, service

DAY = dt.date(2026, 11, 22)
AHRI = catalog.load()["Ahri"]


def png(width=1215, height=717) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 30, 200)).save(buf, "JPEG")
    return buf.getvalue()


class Resp:
    def __init__(self, status, body):
        self.status, self.body = status, body

    async def read(self):
        return self.body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class Session:
    def __init__(self, response):
        self.response = response
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


async def test_spell_hint_is_reencoded_and_does_not_name_the_champion():
    s = Session(Resp(200, png(64, 64)))
    h = await hints.render_hint("spell", AHRI, 1, DAY, s)
    assert s.urls[0] in AHRI.spells
    assert Image.open(io.BytesIO(h.file_bytes)).format == "PNG"
    assert "Ahri" not in h.text
    assert hints.FILENAME == "indice.png"


async def test_splash_hint_is_a_small_crop():
    h = await hints.render_hint("splash", AHRI, 1, DAY, Session(Resp(200, png())))
    image = Image.open(io.BytesIO(h.file_bytes))
    assert image.format == "PNG" and image.width == image.height and image.width < 1215
    assert "Ahri" not in h.text


async def test_letter_hint_needs_no_download():
    s = Session(aiohttp.ClientError())
    h = await hints.render_hint("letter", AHRI, 1, DAY, s)
    assert "**A**" in h.text and h.file_bytes is None and s.urls == []


@pytest.mark.parametrize(
    "response", [Resp(404, b""), Resp(200, b"not an image"), aiohttp.ClientError(), TimeoutError()]
)
async def test_cdn_failure_is_reported(response):
    with pytest.raises(hints.HintUnavailable):
        await hints.render_hint("splash", AHRI, 1, DAY, Session(response))


def test_ledger_label_exists():
    assert "loldle_hint" in REASON_LABELS


@pytest.fixture
def conn():
    c = AsyncMock()
    pool = MagicMock()
    pool.acquire = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=c), __aexit__=AsyncMock(return_value=False))
    )
    c.transaction = MagicMock(
        return_value=AsyncMock(__aenter__=AsyncMock(return_value=None), __aexit__=AsyncMock(return_value=False))
    )
    with (
        patch.object(service, "get_pool", return_value=pool),
        patch.object(service.currency, "get_or_create_wallet", AsyncMock()) as self_wallet,
        patch.object(service.currency, "ensure_house_wallet", AsyncMock()) as house_wallet,
        patch.object(service.currency, "get_balance_locked", AsyncMock(return_value=500)) as balance,
        patch.object(service.currency, "adjust", AsyncMock(return_value=475)) as adjust,
    ):
        c.wallets = (self_wallet, house_wallet)
        c.balance = balance
        c.adjust_mock = adjust
        yield c


async def test_buying_moves_coins_to_the_house_after_creating_both_wallets(conn):
    conn.fetchval.side_effect = [None, 3]  # not solved, 3 guesses
    conn.fetch.return_value = []
    out = await service.buy_hint(1, DAY, 2, "spell")
    assert out.status == "bought" and out.balance == 475
    conn.wallets[0].assert_awaited_once_with(1, 2)
    conn.wallets[1].assert_awaited_once_with(1)
    calls = [(c.kwargs["user_id"], c.kwargs["amount"], c.kwargs["reason"]) for c in conn.adjust_mock.await_args_list]
    assert calls == [(2, -25, "loldle_hint"), (HOUSE_USER_ID, 25, "loldle_hint")]


async def test_owned_hint_is_never_charged_again(conn):
    conn.fetchval.side_effect = [None]
    conn.fetch.return_value = [{"kind": "spell"}]
    assert (await service.buy_hint(1, DAY, 2, "spell")).status == "owned"
    conn.adjust_mock.assert_not_called()


async def test_locked_before_three_guesses_or_out_of_order_or_after_solving(conn):
    conn.fetch.return_value = []
    conn.fetchval.side_effect = [None, 2]
    assert (await service.buy_hint(1, DAY, 2, "spell")).status == "locked"
    conn.fetchval.side_effect = [None, 5]
    assert (await service.buy_hint(1, DAY, 2, "letter")).status == "locked"
    conn.fetchval.side_effect = [1, 5]
    assert (await service.buy_hint(1, DAY, 2, "spell")).status == "locked"
    conn.adjust_mock.assert_not_called()


async def test_too_poor_is_charged_nothing(conn):
    conn.fetchval.side_effect = [None, 3]
    conn.fetch.return_value = []
    conn.balance.return_value = 24
    out = await service.buy_hint(1, DAY, 2, "spell")
    assert out.status == "poor" and out.balance == 24
    conn.adjust_mock.assert_not_called()
