# Lavalink + yt-dlp.
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
# `curl` is deliberately left installed: compose's healthcheck for this service is a curl
# against /v4/info, and removing it would leave lavalink permanently unhealthy — which the bot
# waits on with `condition: service_healthy`, so the whole stack would never come up.
ARG TAG=4

FROM ghcr.io/lavalink-devs/lavalink:${TAG}

# Provided by BuildKit. Only amd64 and arm64 ship a standalone build; the arch is resolved at
# build time so this doesn't silently install an x86 binary on an ARM host.
ARG TARGETARCH

USER root

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl ca-certificates; \
    case "${TARGETARCH}" in \
      amd64) YTDLP_ASSET=yt-dlp_linux ;; \
      arm64) YTDLP_ASSET=yt-dlp_linux_aarch64 ;; \
      *) echo "no standalone yt-dlp build for TARGETARCH=${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fL "https://github.com/yt-dlp/yt-dlp/releases/latest/download/${YTDLP_ASSET}" \
      -o /usr/bin/yt-dlp; \
    chmod +x /usr/bin/yt-dlp; \
    rm -rf /var/lib/apt/lists/*; \
    yt-dlp --version

USER lavalink
