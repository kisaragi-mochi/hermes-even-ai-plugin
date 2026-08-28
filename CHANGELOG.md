# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Accept `is_reconnect` keyword argument in `EvenAIAdapter.connect()` so the adapter binds under Hermes Agent v0.19.0+, whose gateway calls `adapter.connect(is_reconnect=...)` on both first-connect and every reconnect. Without this, the adapter raised `TypeError: got an unexpected keyword argument 'is_reconnect'` on every attempt and never bound its HTTP port. ([#16](https://github.com/kisaragi-mochi/hermes-even-ai-plugin/issues/16))

## [0.2.0] - 2026-05-11

Initial public release of the Even AI Platform Adapter for [Hermes Agent](https://github.com/NousResearch/hermes-agent).

### Added

- First-class Hermes platform registration as `even-ai` via `ctx.register_platform`, alongside Telegram / Discord / LINE / etc.
- Inbound HTTPS endpoint compatible with the Even App "Add Agent" wire format (OpenAI chat-completions style POST).
- Bearer-token authentication via `EVEN_AI_AUTH_TOKEN`, with `provided_len` / `expected_len` logging on `401` for whitespace debugging.
- Session routing keyed by the `x-openclaw-agent-id` header; set `EVEN_AI_ALLOW_ALL_USERS=true` to bypass Hermes' default user-id validation during development.
- Hedged-request handling: the Even App fires two parallel POSTs per turn and adopts whichever responds first; the adapter shares a single in-flight future across both legs via `asyncio.shield` so both legs deliver identical content.
- Measured 30-32 s HUD display deadline (beyond which the Even App overlays its own English "wait" UI). `EVEN_AI_RESPONSE_TIMEOUT` defaults to 28 s to stay under this.
- Two-stage slow-LLM UX: when generation exceeds the timeout, the inbound POST returns a placeholder (`EVEN_AI_SLOW_PLACEHOLDER`) and the real reply is shipped via the Telegram fallback path.
- Telegram fallback delivery (`EVEN_AI_TELEGRAM_FALLBACK=true`) for long replies and proactive sends, using either the live in-process Telegram adapter or the Telegram REST API when running from a different process (e.g. `hermes cron`).
- G2-aware reply formatting: markdown stripping, sentence-boundary-aware truncation at `EVEN_AI_MAX_CHARS` (default 400), `\n` preserved as HUD line breaks.
- `EVEN_AI_DEBUG_ECHO_DELAY` for measuring Even App client / display timeouts without involving an LLM (sleep N seconds, then return a fixed echo).
- `EVEN_AI_HOME_CHAT_ID` (default `even-ai-main`) for `hermes cron` and proactive delivery.
- `EVEN_AI_TELEGRAM_FALLBACK_PREFIX` for distinguishing fallback messages from regular Telegram replies on the user's phone (e.g. `👓 `).
- English and Japanese README documenting setup, environment variables, known pitfalls, and a re-measurement procedure for future Even App updates.

### Requirements

- Hermes Agent v0.13.0 (older versions may lack the plugin platform-registration hooks used here).
- Python 3.11+.
- HTTPS termination in front of `EVEN_AI_BIND_PORT` (Tailscale Funnel / Cloudflare Tunnel / Caddy etc.) — the Even App will not accept plain HTTP URLs in Add Agent.

### Notes

- This is an alpha release. Minor breaking changes can occur during the 0.x series.

[unreleased]: https://github.com/kisaragi-mochi/hermes-even-ai-plugin/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/kisaragi-mochi/hermes-even-ai-plugin/releases/tag/v0.2.0
