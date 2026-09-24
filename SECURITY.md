# Security Policy

## Secrets

Never commit:

- QQ cookies, tickets or login-state files
- AV bridge Bearer Tokens
- Codex / ChatGPT credentials (`auth.json`)
- real QQ numbers in fixtures

Keep the bridge token in a file readable only by the services that need it,
or in `ASTRBOT_QQ_CALL_BRIDGE_TOKEN`. Rotate a credential immediately if it
appears in a terminal transcript, issue, commit or chat.

## Network exposure

The AV bridge handles sensitive QQ process state. It must:

- listen on loopback by default, and at most on a private container network
  when AstrBot runs elsewhere (`ASTRBOT_QQ_CALL_BRIDGE_HOST`)
- require a high-entropy Bearer Token on every endpoint but `/healthz`,
  including the `/v1/stream` WebSocket, which carries call audio
- return only the fields documented in `bridge/PROTOCOL.md`
- redact all cookies, tickets, tokens and native event payloads; AVSDK log
  lines (which carry uids and call parameters) are kept in `/v1/status` only
  when `ASTRBOT_QQ_CALL_AVSDK_LOGS=1` is set for debugging

Do not expose the bridge through a reverse proxy or public firewall rule.

## Reporting

Report security issues through the repository's private security advisory
feature. Do not open a public issue containing credentials or QQ login data.
