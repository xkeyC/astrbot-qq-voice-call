# Security Policy

## Secrets

Never commit:

- model or cloud API keys
- QQ cookies, tickets or login-state files
- AV bridge Bearer Tokens
- cloned-voice enrollment credentials
- real QQ numbers or MaiBot person IDs in fixtures

Use `DASHSCOPE_API_KEY`, `MAIBOT_QQ_CALL_VOICE_ID` and
`MAIBOT_QQ_CALL_BRIDGE_TOKEN` in the service environment. Rotate a credential
immediately if it appears in a terminal transcript, issue, commit or chat.

## Network exposure

The AV bridge handles sensitive QQ process state. It must:

- listen only on loopback or a private Unix socket
- require a high-entropy Bearer Token
- return only the fields documented in `bridge/PROTOCOL.md`
- redact all cookies, tickets, tokens and native event payloads

Do not expose the bridge through a reverse proxy or public firewall rule.

## Reporting

Report security issues through the repository's private security advisory
feature. Do not open a public issue containing credentials or QQ login data.
