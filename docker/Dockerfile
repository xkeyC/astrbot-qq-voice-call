# NapCat + Linux QQ + the AstrBot QQ voice-call bridge, in one container.
#
# Build from the repository root:
#   docker build -f docker/Dockerfile -t astrbot-qq-voice-call .
FROM debian:bookworm-slim

ARG QQ_URL=https://qqdl.gtimg.cn/qqfile/QQNT/9.9.32/beta/fd40a3ec/linuxqq_3.2.30-50969_amd64.deb
ARG NAPCAT_URL=https://github.com/NapNeko/NapCatQQ/releases/latest/download/NapCat.Shell.zip

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl unzip gosu procps util-linux python3 openssl \
      xvfb xauth pulseaudio pulseaudio-utils ffmpeg fonts-noto-cjk \
      libnss3 libgbm1 libasound2 libgtk-3-0 libnotify4 libxss1 libxtst6 \
      libsecret-1-0 libatspi2.0-0 libdrm2 libxkbfile1 libuuid1 \
    && curl -fL --retry 3 -o /tmp/linuxqq.deb "$QQ_URL" \
    && apt-get install -y --no-install-recommends /tmp/linuxqq.deb \
    && rm -f /tmp/linuxqq.deb && rm -rf /var/lib/apt/lists/*

RUN useradd --no-log-init -m -d /app napcat \
    && curl -fL --retry 3 -o /tmp/napcat.zip "$NAPCAT_URL" \
    && mkdir -p /app/napcat && unzip -q /tmp/napcat.zip -d /app/napcat && rm /tmp/napcat.zip \
    && mkdir -p /app/napcat/plugins /app/napcat/config \
    && echo "(async () => {await import('file:///app/napcat/napcat.mjs');})();" \
       > /opt/QQ/resources/app/loadNapCat.js \
    && sed -i 's|"main": "[^"]*"|"main": "./loadNapCat.js"|' /opt/QQ/resources/app/package.json \
    && chown -R napcat:napcat /app /opt/QQ/resources/app

COPY bridge /opt/astrbot-qq-voice-call/bridge
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh /opt/astrbot-qq-voice-call/bridge/scripts/*.sh

WORKDIR /app/napcat
# 6099: NapCat WebUI (QR login); 6110: voice-call bridge (token required)
EXPOSE 6099 6110
VOLUME ["/app/.config/QQ", "/app/napcat/config", "/app/qqcall/runtime"]
ENTRYPOINT ["/entrypoint.sh"]
