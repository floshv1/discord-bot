"""yt-dlp's client order decides whether YouTube playback works at all."""

import re
from pathlib import Path

CONFIG = Path(__file__).resolve().parent.parent / "lavalink" / "application.yml"


def _playback_args() -> list[str]:
    text = CONFIG.read_text(encoding="utf-8")
    match = re.search(r"customPlaybackArgs:\n((?:\s+- .+\n)+)", text)
    assert match, "ytdlp.customPlaybackArgs is missing"
    return [line.strip()[2:].strip().strip('"') for line in match.group(1).splitlines()]


def test_playback_prefers_a_client_without_a_pre_roll_delay():
    # tv_samsung links answer 403 until a pre-roll wait is over. yt-dlp sleeps through it when it
    # downloads; LavaSrc only takes the URL and fetches it at once. The first client wins per format.
    args = _playback_args()
    clients = args[args.index("--extractor-args") + 1]
    assert clients.startswith("youtube:player_client=visionos,")


def test_playback_args_keep_what_lavasrc_parses():
    # Setting custom args replaces LavaSrc's defaults, and anything printed outside the JSON breaks it.
    args = _playback_args()
    assert {"-q", "--no-warnings", "-J"} <= set(args)
    assert args[args.index("-f") + 1] == "bestaudio"
