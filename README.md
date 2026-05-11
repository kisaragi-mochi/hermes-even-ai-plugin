# hermes-even-ai-plugin

**[Even Realities G2](https://www.evenrealities.com/) smart-glasses HUD adapter for [Hermes Agent](https://github.com/NousResearch/hermes-agent), via the Even App's "Add Agent" feature.**

Talk to your Hermes agent through G2 smart glasses. Replies under ~28 s land on the HUD inline; longer replies are routed through Telegram so nothing is lost.

> Languages: English | [日本語](./README.ja.md)
>
> Status: **alpha** — works on real hardware (Even G2 + Even App + Hermes Agent v0.13.0), but the Even App side of the protocol is undocumented and reverse-engineered. Future Even App updates may break things; bug reports welcome.

---

## Why this exists

The Even App lets you "Add Agent" by typing an HTTPS URL and a bearer token. It then forwards G2 voice input as OpenAI-compatible chat-completion `POST` requests, and prints the reply on the HUD.

That makes Even App a perfectly good front-end for any agent you already run on Hermes — Hermes just needs to speak the wire format. This plugin is that bridge.

After connecting:

- G2 voice input is delivered to Hermes as a first-class platform (alongside Telegram / Discord / LINE / etc.) — sessions, memory, skills, MCP are all preserved.
- Short replies appear on the HUD within Even App's ~30 s display deadline.
- Long replies (slow LLM, tool-using chains) overflow to Telegram so you still get the answer.
- `hermes cron deliver=even-ai` jobs also route through the Telegram fallback (Even AI has no server push channel of its own).

---

## Features

- **First-class Hermes platform** — registers `even-ai` via `ctx.register_platform`; appears in `hermes status` next to Telegram et al.
- **Hedged-request safe** — Even App sends two parallel POSTs per turn and adopts whichever response arrives first; the adapter shares a single in-flight future across both so the real reply always wins.
- **Two-stage slow-LLM UX** — under the 28 s budget, the real reply ships on the inbound POST; over budget, a placeholder ships on the POST and the eventual reply is pushed via Telegram fallback.
- **G2-aware formatting** — markdown stripped (no `**bold**` / fenced code / list bullets bleeding through), sentence-boundary-aware truncation at 400 chars, line breaks preserved.
- **Telegram fallback for proactive sends** — `send()` calls from skills / cron / other sessions route through Telegram because Even AI is request/response only.
- **Built-in debug echo mode** — set `EVEN_AI_DEBUG_ECHO_DELAY=N` to bypass the agent and return a fixed reply after N seconds; useful for re-measuring Even App's client behavior when the Even App updates.

---

## Architecture

```
[Even G2]            (BLE)
   │
   ▼
[iPhone Even App "Add Agent"]
   │      HTTPS POST × 2 (hedged request, 1-2 ms apart)
   │      Authorization: Bearer <token>
   │      x-openclaw-agent-id: <agent-id>
   ▼
[even-ai-platform plugin, port 8767]
   │      shared future per chat_id
   │      ├─ POST #1: dispatch → handle_message → wait future
   │      └─ POST #2: detect duplicate → share future → return same content
   ▼
[Hermes Gateway Runner → default profile]
   │
   ▼
OpenAI-compatible chat.completion JSON (non-streamed)
   │
   ▼
[Both POSTs ←] truncated to ≤400 chars, markdown stripped
   │
   ▼
[Even App] adopts whichever response arrives first
   │
   ▼
[G2 HUD] 576×136 monochrome, plain text, \n honored

If the reply exceeds 28 s:
   ├─ POST returns "考え中… (placeholder)" within budget
   └─ Background task awaits the real reply (up to 600 s)
        └─ Telegram Bot API → user's Telegram (push notification)
```

---

## Requirements

- **Hermes Agent v0.13.0** or compatible (tested 2026-05-11). Older versions may lack the plugin platform-registration hooks this adapter uses.
- **Python 3.11+** (Hermes already runs this).
- **`aiohttp`** — bundled with Hermes; no extra install required.
- **Even Realities G2** glasses, paired with the iOS Even App that exposes "Add Agent" under Even AI settings.
- **HTTPS reverse-proxy** in front of port `8767` — Even App refuses plain HTTP. A few good options:
  - Tailscale Funnel
  - Cloudflare Tunnel
  - Caddy / nginx with a real TLS cert
- **(Optional, recommended) Telegram bot** — the same one you already use for Hermes — so long replies and proactive sends can land somewhere when they overflow the HUD budget.

---

## Installation

### 1. Drop the plugin into Hermes

```bash
# Pick whichever you prefer:
#   (a) Clone directly into the user-plugins dir
git clone https://github.com/kisaragi-mochi/hermes-even-ai-plugin.git \
  ~/.hermes/plugins/even-ai

#   (b) Or clone elsewhere and symlink
git clone https://github.com/kisaragi-mochi/hermes-even-ai-plugin.git \
  ~/src/hermes-even-ai-plugin
ln -s ~/src/hermes-even-ai-plugin ~/.hermes/plugins/even-ai
```

The directory name on disk (`even-ai`) is independent of the plugin name (`even-ai-platform`) — Hermes loads `plugin.yaml` to discover the real name.

### 2. Enable the plugin

```bash
hermes plugins enable even-ai-platform
```

This adds `even-ai-platform` to `~/.hermes/config.yaml` under `plugins.enabled` (user plugins are opt-in by default).

### 3. Configure environment variables

Add to `~/.hermes/.env` (minimum):

```bash
# Required
EVEN_AI_AUTH_TOKEN=<a long random string you choose>

# Required for dev (the Even App's "agent-id" is not a real Hermes user_id;
# this tells Hermes to accept it anyway)
EVEN_AI_ALLOW_ALL_USERS=true

# Optional but recommended
EVEN_AI_BIND_PORT=8767
EVEN_AI_RESPONSE_TIMEOUT=28
EVEN_AI_HOME_CHAT_ID=even-ai-main
EVEN_AI_TELEGRAM_FALLBACK=true
EVEN_AI_TELEGRAM_FALLBACK_PREFIX=👓 
```

See [**Configuration**](#configuration) below for the full list.

### 4. Set up your HTTPS front-end

The plugin binds plain HTTP on port `8767`. Put HTTPS in front of it — Even App refuses plain HTTP URLs in the Add Agent dialog. Verify reachability:

```bash
curl -sS https://<your-public-host>/health \
  -H "Authorization: Bearer $EVEN_AI_AUTH_TOKEN"
# → {"status":"ok","platform":"even-ai","max_chars":400,"response_timeout":28.0}
```

### 5. Restart the Hermes gateway

```bash
# macOS launchd
launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway

# Or however you run Hermes
hermes restart
```

Confirm:

```bash
hermes status
# → ... even-ai connected ✓
```

### 6. Add the agent in the iPhone Even App

In the iOS Even App:

1. Even AI → Add Agent
2. **URL**: `https://<your-public-host>/`  (the path is the server root)
3. **Token**: the same value you set as `EVEN_AI_AUTH_TOKEN`
4. **Name** / **Description**: whatever you like — these are display-only
5. Save

Speak to the G2 ("Hey, Even") and the message will hit your Hermes agent. The HUD displays the reply.

---

## Configuration

| Env var | Default | Description |
|---|---|---|
| `EVEN_AI_AUTH_TOKEN` | (required) | Bearer token the Even App must send. Choose a long random string. |
| `EVEN_AI_BIND_HOST` | `0.0.0.0` | Bind address for the inbound HTTP server. |
| `EVEN_AI_BIND_PORT` | `8767` | Bind port. `8765`/`8766` may collide with other tools. |
| `EVEN_AI_ALLOWED_AGENT_IDS` | (any) | Comma-separated `x-openclaw-agent-id` values to allow. Leave empty to allow any. |
| `EVEN_AI_ALLOW_ALL_USERS` | `false` | Set `true` so the agent-id header is accepted as a Hermes user. **Required for dev**: Even App's agent-id is not a real Hermes-registered user. |
| `EVEN_AI_HOME_CHAT_ID` | `even-ai-main` | Default chat_id used by `hermes cron deliver=even-ai` and proactive sends. |
| `EVEN_AI_MAX_CHARS` | `400` | Max characters per HUD reply. The G2 display is 576 × 136 monochrome; ~400 chars is the practical limit. |
| `EVEN_AI_RESPONSE_TIMEOUT` | `28` | Seconds to wait on the inbound POST before shipping a placeholder. **Raising past 30 causes the HUD to override your reply with the Even App's own English "wait" overlay** — see [Background](#background). |
| `EVEN_AI_SLOW_PLACEHOLDER` | `考え中… 続きは Telegram に届けるね。` | Text returned on the POST when the agent reply exceeds the timeout. Override with English if you prefer. |
| `EVEN_AI_TELEGRAM_FALLBACK` | `true` | Whether to route proactive sends and slow-reply overflow through Telegram. Set `false` to drop instead. |
| `EVEN_AI_TELEGRAM_CHAT_ID` | (`TELEGRAM_HOME_CHANNEL`) | Telegram chat_id for fallback delivery. Defaults to whatever Hermes' Telegram adapter uses. |
| `EVEN_AI_TELEGRAM_FALLBACK_PREFIX` | (none) | Prefix prepended to Telegram fallback messages so they're easy to distinguish from regular Telegram replies. e.g. `👓 `. |
| `EVEN_AI_DEBUG_ECHO_DELAY` | `0` | **Debug only.** When > 0, the adapter sleeps N seconds and returns a fixed echo instead of dispatching to the agent. Used to characterize Even App's client-side timeout / retry behavior. Leave at 0 in production. |

---

## Operation modes

### Short reply (≤ 28 s)

```
G2: "Hey Even, what time is it?"
  → Even App POST × 2 (hedged) → adapter → handle_message →
    agent replies "It's 14:32" in 1.2 s →
  → Both POSTs return the same content →
  → Even App adopts the first one →
  → HUD: "It's 14:32"
```

### Long reply (> 28 s — slow LLM / tool chain)

```
G2: "Explain the architecture of the new memory system."
  → POST × 2 → adapter → handle_message →
  → 28 s elapses, agent still thinking →
  → POSTs return "考え中… 続きは Telegram に届けるね。" →
  → HUD: "考え中… 続きは Telegram に届けるね。" (the placeholder)
  → Agent finishes 12 s later →
  → Background task picks up the real reply →
  → Telegram Bot API → user's Telegram → push notification →
  → User reads the full answer on their phone
```

### Proactive send (skill / cron / other session)

```
Skill: send("even-ai-main", "Don't forget the 3pm meeting")
  → No pending POST → Telegram fallback path →
  → Telegram Bot API → user's Telegram → push notification
```

This is intentional: the Even AI wire protocol has no server-push channel, so the only way to reach the user when no POST is pending is via a different platform. Telegram is the recommended fallback because most Hermes setups already have it configured.

---

## Troubleshooting

### HUD shows nothing / Even App keeps loading forever

- **`401 unauthorized`** — token mismatch. Check `EVEN_AI_AUTH_TOKEN` and the token in the Even App's Add Agent dialog. The adapter logs `provided_len=X expected_len=Y` on 401, which makes whitespace / paste errors obvious.
- **TCP-level reach** — `curl -sS https://<host>/health -H "Authorization: Bearer $TOKEN"` from a machine off your LAN. If that fails, the HTTPS front-end isn't routing.
- **Plain HTTP** — Even App refuses non-HTTPS URLs. You **must** terminate TLS in front of port 8767.
- **`Unauthorized user: ... Dropping message from unauthorized user`** in the agent log — set `EVEN_AI_ALLOW_ALL_USERS=true`. The Even App's `x-openclaw-agent-id` is not a real Hermes user_id and the gateway's default user-auth check rejects it.

### HUD shows an English "wait" message instead of my agent's reply

- Your reply took longer than ~30 s. Even App has an undocumented HUD-display deadline at 30-32 s, after which it overrides server responses with its own wait overlay (even though our 200 reached it).
- Check `EVEN_AI_RESPONSE_TIMEOUT`: it must stay **≤ 28** (the safe upper bound). The plugin already ships placeholders to the HUD at the 28 s mark, which is what you want.
- Consider switching to a faster model for the Even AI session if reasoning models routinely overshoot.

### HUD shows the placeholder but Telegram never gets the real reply

- `EVEN_AI_TELEGRAM_FALLBACK=true` (default) and either `EVEN_AI_TELEGRAM_CHAT_ID` or `TELEGRAM_HOME_CHANNEL` must be set.
- Telegram's `TELEGRAM_BOT_TOKEN` env must be present in Hermes' environment (the plugin imports `tools.send_message_tool._send_telegram` for the out-of-process path).
- Check the agent log for `[EvenAI] direct Telegram send failed` or `[EvenAI] no TELEGRAM_BOT_TOKEN for fallback`.

### `Plugin 'even-ai-platform' has no register() function`

- The `__init__.py` must re-export `register`. Make sure it contains:

  ```python
  from .adapter import register
  __all__ = ["register"]
  ```

- This package already does this; if you customize, don't drop it.

### Hedged-request placeholder leaks onto the HUD

- This happens if you've replaced `_handle_post` with a "first-wins placeholder" strategy. **Don't.** Even App sends two parallel POSTs per turn and adopts whichever arrives first. If one POST returns a placeholder immediately while the other is still computing, the placeholder wins and the real reply is discarded.
- The shipped implementation handles this correctly: the second POST detects the in-flight future for the same `chat_id` and shares it via `asyncio.shield`, so both POSTs return the same content.

---

## Background

This section documents the Even App behavior the adapter has to work around. All numbers are measured on real hardware on 2026-05-11 against Hermes Agent v0.13.0.

### Wire format

```
POST <root>
Authorization: Bearer <token>
Content-Type: application/json
User-Agent: Dart/3.8 (dart:io)
x-openclaw-agent-id: <agent-id>          # e.g. "main"

{"model":"openclaw","messages":[{"role":"user","content":"..."}]}
```

- **Response**: non-streamed OpenAI-compatible `chat.completion` JSON. SSE is *not* required.
- **`\n`** in `content` is honored as a line break on the HUD.
- The path is the **server root**, not `/v1/chat/completions`.

### Hedged-request pattern

The Even App sends **two parallel POSTs per voice turn**, 1-2 ms apart, both with identical headers and body. It adopts whichever response arrives first and silently discards the later one.

Practical consequences:

1. The adapter must reply to **both** POSTs with the same content (otherwise the user randomly sees stale or empty replies).
2. A naive "second POST gets a placeholder while first POST computes" strategy is wrong: the placeholder is faster, so the placeholder wins and the real reply is thrown away. The shipped code uses `asyncio.shield(existing)` so both POSTs await the same future.

### Display deadline (not a TCP timeout)

| N (seconds) | TCP socket closes | HUD shows |
|---|---|---|
| 5 | no | real reply ✅ |
| 10 | no | real reply ✅ |
| 15 | no | real reply ✅ |
| 25 | no | real reply ✅ |
| 28 | no | real reply ✅ |
| 30 | no | real reply ✅ |
| **32** | no | English "wait" overlay ❌ |
| 45 | no | English "wait" overlay ❌ |

Two distinct timeouts are at play:

- **TCP client timeout** — > 45 s. The socket stays open well past the display deadline; `request.transport.is_closing()` does not become `True`.
- **Display deadline** — 30-32 s. Even App switches the HUD to its own English wait overlay once this elapses, regardless of whether the server has already responded with HTTP 200.

The plugin defaults `EVEN_AI_RESPONSE_TIMEOUT=28` to leave a 2-4 s margin below the 30 s display cutoff. Raising it past 30 will cause the HUD to override your replies with the wait overlay.

### Re-measuring on a new Even App version

The Even App is closed-source and may change. To re-measure:

```bash
# Set the adapter to "sleep N then echo" mode:
EVEN_AI_DEBUG_ECHO_DELAY=10  hermes restart

# Speak to the G2 and watch the HUD + Hermes log.
# Repeat with N = 5, 10, 15, 25, 28, 30, 32, 45.
# Adjust EVEN_AI_RESPONSE_TIMEOUT based on the new display deadline.
# Set EVEN_AI_DEBUG_ECHO_DELAY=0 to return to production.
```

The adapter logs hedged-request detection, TCP disconnect timestamps, and `retry_seen` flags during debug echo so you can correlate against the configured delay.

---

## Compatibility

- **Tested**: Hermes Agent v0.13.0, macOS launchd Hermes gateway, iOS Even App (Even AI feature), Even Realities G2 firmware as of 2026-05-11.
- **Untested but expected to work**: Linux Hermes installs, Hermes Agent v0.12.0+ (the `register_platform` API surface used by this plugin has been stable).
- **Known not to work**: Hermes Agent before v0.12.0 — earlier versions lack the platform-plugin extension points.

---

## Contributing

Bug reports and PRs welcome — see [CONTRIBUTING.md](./CONTRIBUTING.md) for the full development guide (setup, branch / commit / PR conventions, dual-language sync rule). Especially welcome:

- Measurements on a newer Even App that change the display-deadline number.
- Linux deployment notes (the plugin should work, but the test rig is macOS).
- Additional platform fallbacks (Discord / LINE / Slack) for the overflow path.

The codebase is small (one `adapter.py`, ~1000 lines) and self-contained. The `test_formatting.py` file covers markdown stripping and sentence-aware truncation; please add cases for any formatting tweaks.

---

## License

[MIT](./LICENSE).

---

## Changelog

See [CHANGELOG.md](./CHANGELOG.md) for release history.

---

## See also

- [Hermes Agent](https://github.com/NousResearch/hermes-agent) — the agent platform this plugs into.
- [awesome-hermes-agent](https://github.com/0xNyk/awesome-hermes-agent) — community plugin / tool list.
- [Even Realities G2](https://www.evenrealities.com/) — the glasses.
- [Hermes — Adding a Platform Adapter](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/developer-guide/adding-platform-adapters.md) — the developer guide used to author this plugin.
