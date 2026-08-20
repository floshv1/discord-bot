# Lavalink + yt-dlp.
#
# LavaSrc's `ytdlp` source shells out to a real `yt-dlp` binary, and the official Lavalink
# image has no such thing. Upstream publishes a ready-made image for this
# (`ghcr.io/topi314/lavasrc`) built from a Dockerfile identical to the one below — but it was
# last pushed seven months ago, so the yt-dlp baked into it is seven months old. Since the
# entire reason to use yt-dlp is that it keeps up with YouTube's changes within days, a stale
# copy of it is worth exactly as much as the youtube-source plugin it replaces. We build our
# own so a rebuild always pulls the current release.
#
# `curl` is deliberately left installed: compose's healthcheck for this service is a curl
# against /v4/info, and removing it would leave lavalink permanently unhealthy — which the bot
# waits on with `condition: service_healthy`, so the whole stack would never come up.
ARG TAG=4

FROM ghcr.io/lavalink-devs/lavalink:${TAG}

USER root

RUN apt-get update && \
    apt-get install -y --no-install-recommends python3 curl ca-certificates && \
    curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/bin/yt-dlp && \
    chmod +x /usr/bin/yt-dlp && \
    rm -rf /var/lib/apt/lists/*

USER lavalink
