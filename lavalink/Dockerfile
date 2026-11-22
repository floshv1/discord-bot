# Lavalink + yt-dlp (+ Deno, which yt-dlp needs for YouTube).
#
# LavaSrc's `ytdlp` source shells out to a real `yt-dlp` binary, and the official Lavalink
# image has no such thing. Upstream publishes a ready-made image for this
# (`ghcr.io/topi314/lavasrc`) built from a Dockerfile much like this one — but it was last
# pushed seven months ago, so the yt-dlp baked into it is seven months old. Since the entire
# reason to use yt-dlp is that it keeps up with YouTube's changes within days, a stale copy of
# it is worth exactly as much as the youtube-source plugin it replaced. We build our own so a
# rebuild always pulls the current release.
#
# We install the **standalone** build (`yt-dlp_linux`), not the small `yt-dlp` zipimport one,
# and deliberately do NOT install python3. This is not a preference:
#
#   Failed to parse yt-dlp output as JSON: Deprecated Feature: Support for Python version
#   3.10 has been deprecated. Please update to Python 3.11 or above
#
# The base image is Ubuntu jammy, whose `python3` is 3.10. yt-dlp then prints that warning to
# **stdout, ahead of the JSON**, and LavaSrc parses the whole stream in one go — so it dies on
# the word `Deprecated` at column 11 while the search results sit right behind it, perfectly
# valid. Installing a newer python would fix today's message and leave the shape of the bug in
# place; the standalone binary bundles its own interpreter, so there is no system python to be
# wrong about. Anything yt-dlp prints outside the JSON breaks the parse, which is worth
# remembering before adding flags here.
#
# Deno: yt-dlp needs an external JavaScript runtime to solve YouTube's player challenges, and
# the standalone build bundles Python but not that. Without one, YouTube formats go missing —
# a failure that looks exactly like YouTube refusing us again. yt-dlp finds `deno` on PATH by
# default, so installing it is the whole configuration. Deno is a single static binary, so
# this does not reintroduce the system-interpreter trap above.
#
# Both downloads are checked against the SHA-256 sums their release publishes. They run as
# root at build time and then for every track, so "whatever came back from the URL" is not
# good enough.
#
# `curl` is deliberately left installed: compose's healthcheck for this service is a curl
# against /v4/info, and removing it would leave lavalink permanently unhealthy — which the bot
# waits on with `condition: service_healthy`, so the whole stack would never come up.

# Pinned so two builds of the same commit run the same Lavalink. yt-dlp and Deno stay on
# `latest` on purpose — their freshness is the point — and their versions are recorded in
# /opt/versions.txt (and printed in the build log) so a playback regression can be traced.
ARG TAG=4.2.2

FROM ghcr.io/lavalink-devs/lavalink:${TAG}

# Provided by BuildKit. Only amd64 and arm64 ship a standalone build; the arch is resolved at
# build time so this doesn't silently install an x86 binary on an ARM host.
ARG TARGETARCH

USER root

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates unzip; \
    case "${TARGETARCH}" in \
      amd64) YTDLP_ASSET=yt-dlp_linux;         DENO_ASSET=deno-x86_64-unknown-linux-gnu.zip ;; \
      arm64) YTDLP_ASSET=yt-dlp_linux_aarch64; DENO_ASSET=deno-aarch64-unknown-linux-gnu.zip ;; \
      *) echo "no standalone yt-dlp/deno build for TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    tmp="$(mktemp -d)"; cd "$tmp"; \
    \
    curl -fsSLO "https://github.com/yt-dlp/yt-dlp/releases/latest/download/${YTDLP_ASSET}"; \
    curl -fsSLO "https://github.com/yt-dlp/yt-dlp/releases/latest/download/SHA2-256SUMS"; \
    grep " ${YTDLP_ASSET}\$" SHA2-256SUMS | sha256sum -c -; \
    install -m 0755 "${YTDLP_ASSET}" /usr/bin/yt-dlp; \
    \
    curl -fsSLO "https://github.com/denoland/deno/releases/latest/download/${DENO_ASSET}"; \
    curl -fsSLO "https://github.com/denoland/deno/releases/latest/download/${DENO_ASSET}.sha256sum"; \
    sha256sum -c "${DENO_ASSET}.sha256sum"; \
    unzip -q "${DENO_ASSET}" deno; \
    install -m 0755 deno /usr/bin/deno; \
    \
    cd /; rm -rf "$tmp"; \
    apt-get purge -y --auto-remove unzip; \
    rm -rf /var/lib/apt/lists/*; \
    { echo "yt-dlp $(yt-dlp --version)"; deno --version | head -n1; } | tee /opt/versions.txt

USER lavalink
