# The official NapCat image plus the AstrBot QQ voice-call bridge.
#
# Build from the repository root:
#   docker build -f docker/Dockerfile -t astrbot-qq-voice-call .
# Run like the NapCat image (same volumes and variables), plus port 6110.
FROM mlikiowa/napcat-docker:latest

# linuxqq is installed with --force-depends upstream and lacks xdg-utils; apt
# refuses any other package until that dependency is installed on its own.
RUN apt-get update \
    && apt-get install -y --no-install-recommends xdg-utils \
    && apt-get install -y --no-install-recommends pulseaudio pulseaudio-utils \
    && rm -rf /var/lib/apt/lists/*

COPY bridge /opt/astrbot-qq-voice-call/bridge
COPY docker/entrypoint.sh /astrbot-qq-call-entrypoint.sh
RUN chmod 0755 /astrbot-qq-call-entrypoint.sh /opt/astrbot-qq-voice-call/bridge/scripts/*.sh

# 6099: NapCat WebUI (QR login); 6110: voice-call bridge (token required)
EXPOSE 6099 6110
VOLUME ["/app/qqcall/runtime"]
ENTRYPOINT ["/astrbot-qq-call-entrypoint.sh"]
