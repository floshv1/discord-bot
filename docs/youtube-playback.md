# YouTube playback — how it works and how we got here

The rules live in `CLAUDE.md`; this is the history behind them, kept here so the context file
stays short.

## Why youtube-plugin was removed (not disabled)

Both `youtube-plugin` and LavaSrc's `ytdlp` source register the `ytsearch:` prefix and both
claim `youtube.com` / `youtu.be` URLs. Loading the two leaves which one answers **undefined** —
and the failure mode is the worst kind: yt-dlp looks broken while never actually being called.

The plugin was failing 100% of tracks through three walls in a row, each of which looked like
the others and none of which a version bump fixed:

1. **Local signature-cipher extraction died** — `Must find sig function from script: .../base.js`.
   Upstream closed the report as *not planned* and pointed at a remote cipher server
   (`ghcr.io/kikkia/yt-cipher`) instead.
2. **With the cipher unblocked, YouTube's anti-bot check refused every client**, each wearing a
   different error:
   - `TVHTML5_SIMPLY`: "Sign in to confirm you're not a bot"
   - `ANDROID_VR`: "requires login"
   - `MWEB`: 403 on the stream
   - `WEB`: `No supported audio streams available, available types: ` with an **empty** list
3. **The documented answers trade the problem for a credential.** OAuth applies to the `TV`
   client and nothing else (and upstream warns the account may be terminated); poToken applies
   to `WEB`/`WEBEMBEDDED` and nothing else, and expires.

yt-dlp ships fixes for YouTube's changes within days, which none of the above does.

**Lesson: read a moving error as progress, not a fix.** A cipher failure that becomes an empty
format list has been *unblocked into the next wall*, and each wall imitated the previous one.

## The image (`lavalink/Dockerfile`)

- **Built, not pulled.** Upstream's `ghcr.io/topi314/lavasrc` image is built from a near-identical
  Dockerfile, but was last pushed seven months before we switched — its yt-dlp was seven months
  old, worth exactly as much as the plugin it replaced.
- **Standalone `yt-dlp_linux`, no `python3`.** The zipimport `yt-dlp` runs on the system python;
  the base image is Ubuntu jammy (`python3` 3.10), and yt-dlp then prints
  `Deprecated Feature: Support for Python version 3.10 has been deprecated` **to stdout, ahead of
  the JSON**. LavaSrc parses the whole stream at once and dies at column 11 while valid results
  sit right behind it. Anything yt-dlp prints outside the JSON breaks the parse.
- **Deno is installed** because yt-dlp needs an external JavaScript runtime to solve YouTube's
  player challenges; without one, formats go missing — which looks exactly like YouTube
  refusing us again. yt-dlp picks `deno` up from PATH with no configuration.
- **Both binaries are SHA-256-verified** against the sums their release publishes.
- **Lavalink is pinned** (`ARG TAG`); yt-dlp and Deno float on `latest` on purpose, and their
  versions are written to `/opt/versions.txt`
  (`docker compose exec lavalink cat /opt/versions.txt`).
- **`curl` stays installed** — the compose healthcheck uses it, and the bot waits on
  `service_healthy`; purging it deadlocks the stack at boot.

## Search prefix and autoplay

wavelink's `Playable.search` defaults to `ytmsearch:` (YouTube Music), which was answered by
youtube-plugin's `MUSIC` client. Nothing registers it any more, so every `/play` would come back
empty — indistinguishable from a bad query. The bot passes `TrackSource.YouTube` (`ytsearch:`),
locked by `tests/test_music.py`.

Autoplay still works: LavaSrc's `ytdlp` `getSourceName()` returns `"youtube"`, which is what
wavelink's autoplay filters on before building its `music.youtube.com/...&list=RD...` mix URL,
and that URL matches ytdlp's own `URL_PATTERN`. Spotify links are replayed through `ytsearch:`
by `lavasrc.providers`, so they go through yt-dlp too.

## Lavalink's `http` source is off

With it on, `/play http://db:5432` (or any internal hostname) makes Lavalink issue the request
from inside the Docker network — a way for any member to probe services that are deliberately
never published. Nothing we play needs it.
