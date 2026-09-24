# Third-party notices

This repository contains original integration code and does not redistribute
AstrBot, NapCat, QQ, model weights or provider SDK source.

## maibot-qq-voice-call

This project is derived from maibot-qq-voice-call by ClaudiaGardner
(GPL-3.0-only): the QQ AV bridge under `bridge/` is its work, adapted for
AstrBot. The MaiBot plugin code was replaced.

<https://github.com/ClaudiaGardner/maibot-qq-voice-call>

## AstrBot

AstrBot is available under AGPL-3.0. This plugin runs inside AstrBot and uses
its voice session module (`astrbot.core.voice`) of the Codex fork.

<https://github.com/xkeyC/AstrBot>

## aiohttp

`aiohttp` is a runtime dependency distributed separately under Apache-2.0.

<https://github.com/aio-libs/aiohttp>

## NapCatQQ and QQ

NapCatQQ has its own limited redistribution terms. This repository contains an
original external plugin that runs against a user-installed NapCat instance; it
does not contain NapCat source or binaries. Users are responsible for the
applicable licenses and platform terms.

<https://github.com/NapNeko/NapCatQQ>

QQ is a Tencent service and is not affiliated with this project. The AV Host
loads `libAVSDKPlugin.so` from the user's own QQ installation; the library is not
copied into releases or this repository.

## Cloud models

Voice runs on OpenAI's Codex realtime service through the user's own ChatGPT
account. No credentials are included; use is subject to OpenAI's terms.
