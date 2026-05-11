"""Even AI Platform Adapter for Hermes Agent.

Receives OpenAI chat-completions POST requests from the Even App's
"Add Agent" feature (which forwards Even G2 smart-glasses voice input),
routes them into Hermes as a first-class platform, and replies inline
within the ~30 second Even AI deadline.

Wire format (confirmed on real hardware 2026-05-11):

    POST <root>
    Authorization: Bearer <token>
    Content-Type: application/json
    User-Agent: Dart/3.8 (dart:io)
    x-openclaw-agent-id: <agent-id>          # e.g. "main"

    {"model":"openclaw","messages":[{"role":"user","content":"..."}]}

Response: non-streamed OpenAI-compatible chat.completion JSON.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import uuid
from typing import Any, Dict, List, Optional

try:
    from aiohttp import web

    AIOHTTP_AVAILABLE = True
except ImportError:  # pragma: no cover - hermes core ships aiohttp
    web = None  # type: ignore[assignment]
    AIOHTTP_AVAILABLE = False

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)

logger = logging.getLogger(__name__)

DEFAULT_BIND_HOST = "0.0.0.0"
DEFAULT_BIND_PORT = 8767  # 8765/8766 used by stackchan-mcp
DEFAULT_MAX_CHARS = 400
# Two-stage UX (Phase 4+5), tuned with measurement on 2026-05-11:
#   * Within 28s → ship the real reply on the original POST.
#   * After 28s → ship a "考え中… Telegram に届けるね" placeholder on the
#     POST and let the real reply arrive via Telegram fallback once the
#     agent finishes.
#
# Measurement findings (debug echo at N seconds):
#   * Even App opens TWO parallel POSTs per voice turn (hedged request)
#     and ADOPTS THE FIRST RESPONSE TO ARRIVE.  The late response is
#     silently discarded.  → both POSTs must reply with the same content.
#   * Socket-level client timeout is > 45s.  No TCP disconnect observed
#     up to N=45.  Earlier "Even AI client timeout ~10-15s" guidance in
#     Phase 0 notes was incorrect.
#   * Even App's *display* deadline is ~30s — between N=30 (HUD shows
#     the real reply) and N=32 (HUD shows an English "wait" notice that
#     overrides our 200 response, even though the response arrived).
#   * 28s is the safe upper bound: leaves a 2-4s margin below the 30s
#     display cutoff while covering most reasoning-model latencies.
DEFAULT_RESPONSE_TIMEOUT = 28.0
DEFAULT_SLOW_PLACEHOLDER = "考え中… 続きは Telegram に届けるね。"
DEFAULT_DISCONNECT_NOTICE = "（接続が切れたよ。後でまた話しかけてね）"
DEFAULT_PROACTIVE_PREFIX = "[Even AI proactive] "
HEALTH_PATH = "/health"


# ---------------------------------------------------------------------------
# Markdown stripping (Phase 3 — kept minimal here, expand if needed)
# ---------------------------------------------------------------------------


def _strip_markdown(text: str) -> str:
    """Convert basic markdown to plain text for the G2 HUD.

    Even G2 cannot render markdown — bold/italic markers, fenced code,
    inline code backticks, link/image syntax, list bullets, and ATX
    headings all appear as literal characters. Strip them to keep the
    HUD readable.
    """
    # Code fences: ```lang ... ``` → ... (drop fence markers)
    text = re.sub(r"```\w*\n?", "", text)
    text = text.replace("```", "")
    # Bold: **text** / __text__ → text
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    # Italic: *text* / _text_ → text
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", text)
    text = re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"\1", text)
    # Inline code: `text` → text
    text = re.sub(r"`(.+?)`", r"\1", text)
    # Images: ![alt](url) → alt  (must come BEFORE links)
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"\1", text)
    # Links: [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
    # Heading markers at line start: # foo → foo
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Blockquotes: > foo → foo
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    # Unordered list bullets at line start: - / * / + → (drop)
    text = re.sub(r"^[\-\*\+]\s+", "", text, flags=re.MULTILINE)
    # Ordered list markers at line start: 1. / 12. → (drop)
    text = re.sub(r"^\d+\.\s+", "", text, flags=re.MULTILINE)
    # Horizontal rules: --- / *** / ___ on their own line → blank
    text = re.sub(r"^[\-\*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    # Table separator rows: |---|---| → drop
    text = re.sub(r"^\s*\|?[\s\-:|]+\|[\s\-:|]+\|?\s*$", "", text, flags=re.MULTILINE)
    # Collapse 3+ consecutive newlines (from stripped blocks) to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


# Sentence-boundary characters preferred for natural truncation.
# Ordered by priority: full-stop punctuation first, then comma-class,
# then any whitespace (newline / space).
_SENTENCE_END_PRIMARY = ("。", "！", "？", "!", "?", ".", "\n")
_SENTENCE_END_SECONDARY = ("、", ",", "；", ";", "　", " ")


def _find_natural_cutoff(text: str, limit: int) -> int:
    """Return the index *after* the best sentence boundary at or before ``limit``.

    Search backward from ``limit`` for a primary terminator (。！？.!?\\n).
    If found within the "good" zone (≥ limit*0.5), cut just past it so the
    last sentence stays whole.  Otherwise fall back to a secondary
    boundary (、,；; or whitespace) within the same zone.  If no boundary
    is reachable, cut at ``limit`` as a last resort.
    """
    if limit <= 0 or limit >= len(text):
        return limit
    floor = max(1, limit // 2)

    # Pass 1: primary sentence terminators — break *after* them so the
    # punctuation stays attached to the preceding clause.
    best = -1
    for end_char in _SENTENCE_END_PRIMARY:
        idx = text.rfind(end_char, floor, limit)
        if idx >= 0:
            # +1 to include the terminator itself in the kept span
            best = max(best, idx + 1)
    if best > 0:
        return best

    # Pass 2: secondary boundaries — break *after* the boundary too.
    for end_char in _SENTENCE_END_SECONDARY:
        idx = text.rfind(end_char, floor, limit)
        if idx >= 0:
            best = max(best, idx + 1)
    if best > 0:
        return best

    return limit


def _truncate_for_g2(content: str, max_chars: int) -> str:
    """Truncate ``content`` so it fits the G2 HUD.

    Strips markdown first, then searches for a Japanese-friendly
    sentence boundary at or before ``max_chars`` so the cut doesn't
    fall mid-clause.  Falls back to a hard cut + ellipsis when no
    boundary is reachable.

    The ellipsis only appears when the cut is *not* on a sentence
    terminator — landing on ``。``/``！``/``？`` already signals
    completeness to the reader.
    """
    cleaned = _strip_markdown(content).strip()
    if len(cleaned) <= max_chars:
        return cleaned

    # Reserve one character for the trailing ellipsis we might add.
    budget = max_chars - 1
    cut = _find_natural_cutoff(cleaned, budget)
    head = cleaned[:cut].rstrip()
    if not head:
        # Pathological input — fall back to a hard cut.
        return cleaned[: max_chars - 1].rstrip() + "…"

    if head[-1] in _SENTENCE_END_PRIMARY:
        # Clean sentence break — no ellipsis needed.
        return head
    return head + "…"


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class EvenAIAdapter(BasePlatformAdapter):
    """HTTP server adapter for the Even AI OpenAI-compatible callback.

    One inbound POST → one ``handle_message`` → one ``send`` resolves the
    pending future and the POST response is returned synchronously.
    Proactive sends (no pending POST) are deferred to Phase 5.
    """

    def __init__(self, config: PlatformConfig, **_: Any) -> None:
        super().__init__(config=config, platform=Platform("even-ai"))
        extra = getattr(config, "extra", {}) or {}

        self.host = str(
            os.getenv("EVEN_AI_BIND_HOST")
            or extra.get("bind_host")
            or DEFAULT_BIND_HOST
        )
        self.port = int(
            os.getenv("EVEN_AI_BIND_PORT")
            or extra.get("bind_port")
            or DEFAULT_BIND_PORT
        )
        self.token = str(
            os.getenv("EVEN_AI_AUTH_TOKEN") or extra.get("token") or ""
        )
        self.max_chars = int(
            os.getenv("EVEN_AI_MAX_CHARS")
            or extra.get("max_chars")
            or DEFAULT_MAX_CHARS
        )
        self.response_timeout = float(
            os.getenv("EVEN_AI_RESPONSE_TIMEOUT")
            or extra.get("response_timeout")
            or DEFAULT_RESPONSE_TIMEOUT
        )
        self.slow_placeholder = str(
            os.getenv("EVEN_AI_SLOW_PLACEHOLDER")
            or extra.get("slow_placeholder")
            or DEFAULT_SLOW_PLACEHOLDER
        )

        # Telegram fallback for proactive sends + slow-LLM overflow.
        # Disabled when chat_id / token are missing; the adapter still
        # works inline within the 22s window, slow responses just get
        # logged and dropped instead of being delivered.
        self.telegram_fallback_enabled = _truthy(
            os.getenv("EVEN_AI_TELEGRAM_FALLBACK", "true")
        )
        self.telegram_fallback_chat_id = (
            os.getenv("EVEN_AI_TELEGRAM_CHAT_ID")
            or os.getenv("TELEGRAM_HOME_CHANNEL")
            or extra.get("telegram_fallback_chat_id")
            or ""
        ).strip()
        self.telegram_fallback_prefix = str(
            os.getenv("EVEN_AI_TELEGRAM_FALLBACK_PREFIX", "")
        )

        # ── DEBUG: client timeout measurement ─────────────────────────────
        # When EVEN_AI_DEBUG_ECHO_DELAY > 0, the adapter bypasses the
        # gateway runner and returns a fixed echo reply after sleeping
        # for the configured number of seconds.  Used to characterize
        # Even AI's client-side timeout / retry / disconnect behavior
        # without burning real LLM tokens.
        try:
            self.debug_echo_delay = float(
                os.getenv("EVEN_AI_DEBUG_ECHO_DELAY", "0") or 0
            )
        except ValueError:
            self.debug_echo_delay = 0.0

        # Pending response futures keyed by chat_id. ``send`` resolves the
        # future so the inbound POST handler can return the agent reply
        # within the 30-second Even AI deadline.
        self._pending: Dict[str, asyncio.Future] = {}
        # Track background tasks that ship overflow replies to the
        # Telegram fallback path so disconnect can drain them cleanly.
        self._overflow_tasks: set[asyncio.Task] = set()

        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None

    @property
    def name(self) -> str:
        return "Even AI"

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def connect(self) -> bool:
        if not AIOHTTP_AVAILABLE:
            logger.error("[EvenAI] aiohttp not installed")
            self._set_fatal_error(
                "aiohttp_missing",
                "aiohttp must be installed for the Even AI adapter",
                retryable=False,
            )
            return False
        if not self.token:
            logger.error("[EvenAI] EVEN_AI_AUTH_TOKEN must be set")
            self._set_fatal_error(
                "config_missing",
                "EVEN_AI_AUTH_TOKEN must be set",
                retryable=False,
            )
            return False

        try:
            self._app = web.Application()
            self._app.router.add_get(HEALTH_PATH, self._handle_health)
            self._app.router.add_post("/", self._handle_post)
            self._runner = web.AppRunner(self._app)
            await self._runner.setup()
            self._site = web.TCPSite(self._runner, self.host, self.port)
            await self._site.start()
        except OSError as exc:
            logger.error(
                "[EvenAI] failed to bind %s:%s — %s", self.host, self.port, exc,
            )
            self._set_fatal_error(
                "bind_failed", f"Could not bind {self.host}:{self.port}: {exc}",
                retryable=True,
            )
            await self._cleanup()
            return False
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("[EvenAI] unexpected error during connect: %s", exc)
            self._set_fatal_error("connect_failed", str(exc), retryable=True)
            await self._cleanup()
            return False

        self._mark_connected()
        logger.info(
            "[EvenAI] HTTP server listening on %s:%s (token=***%s)",
            self.host, self.port, self.token[-4:] if len(self.token) >= 4 else "",
        )
        return True

    async def disconnect(self) -> None:
        # Resolve any pending futures so awaiting POSTs unblock with a
        # graceful placeholder rather than hanging until timeout.
        for chat_id, future in list(self._pending.items()):
            if not future.done():
                future.set_result(DEFAULT_DISCONNECT_NOTICE)
            self._pending.pop(chat_id, None)
        # Cancel any background overflow-delivery tasks. They were
        # bridging slow replies into Telegram — if the gateway is
        # shutting down, that bridge is gone too.
        for task in list(self._overflow_tasks):
            if not task.done():
                task.cancel()
        if self._overflow_tasks:
            await asyncio.gather(
                *self._overflow_tasks, return_exceptions=True
            )
        self._overflow_tasks.clear()
        await self._cleanup()
        self._mark_disconnected()
        logger.info("[EvenAI] disconnected")

    async def _cleanup(self) -> None:
        self._site = None
        if self._runner is not None:
            try:
                await self._runner.cleanup()
            except Exception:
                logger.debug("[EvenAI] runner.cleanup raised", exc_info=True)
            self._runner = None
        self._app = None

    # ── Inbound HTTP ──────────────────────────────────────────────────────

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "status": "ok",
                "platform": "even-ai",
                "max_chars": self.max_chars,
                "response_timeout": self.response_timeout,
            }
        )

    async def _handle_post(self, request: web.Request) -> web.Response:
        # Bearer auth — be lenient about surrounding whitespace because
        # the Even App lets the user paste/type the token by hand and
        # iOS keyboards occasionally append a trailing space or newline.
        auth_header = request.headers.get("Authorization", "").strip()
        if not auth_header.startswith("Bearer "):
            return web.json_response({"error": "missing bearer token"}, status=401)
        provided = auth_header[len("Bearer ") :].strip()
        if not _constant_time_equals(provided, self.token):
            logger.warning(
                "[EvenAI] rejected POST with bad token from %s (provided_len=%d expected_len=%d)",
                request.remote, len(provided), len(self.token),
            )
            return web.json_response({"error": "unauthorized"}, status=401)

        try:
            body = await request.json()
        except Exception as exc:
            return web.json_response({"error": f"invalid json: {exc}"}, status=400)

        if not isinstance(body, dict):
            return web.json_response({"error": "body must be a JSON object"}, status=400)
        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            return web.json_response({"error": "messages[] required"}, status=400)

        last_msg = messages[-1]
        if not isinstance(last_msg, dict):
            return web.json_response({"error": "invalid message entry"}, status=400)
        user_text = str(last_msg.get("content") or "").strip()
        if not user_text:
            return web.json_response({"error": "empty content"}, status=400)

        agent_id = (
            request.headers.get("x-openclaw-agent-id")
            or request.headers.get("X-Openclaw-Agent-Id")
            or "main"
        )
        chat_id = f"even-ai-{agent_id}"
        chat_name = f"Even AI ({agent_id})"

        # ── DEBUG ECHO MODE ──────────────────────────────────────────────
        # Sleep ``debug_echo_delay`` seconds and return a fixed echo so
        # we can characterize Even AI's client-side timeout / retry /
        # disconnect behavior without invoking the agent.  Logs the POST
        # arrival, the sleep, the disconnect status, and whether a retry
        # POST landed during the wait.
        if self.debug_echo_delay > 0:
            return await self._debug_echo_handler(
                request, body, chat_id, user_text,
            )

        # Hedged-request handling: the Even App sends two parallel POSTs
        # at the start of every voice turn and adopts whichever response
        # arrives FIRST (the late one is discarded).  An earlier "first-
        # wins placeholder" approach therefore guaranteed the placeholder
        # would be the surviving response on the HUD — exactly backwards.
        #
        # Instead, when we detect a duplicate POST while the first is
        # still in flight, share the same future: both POSTs await the
        # same agent reply, so whichever Even App picks up first, the
        # user sees the real reply.  The slow-LLM placeholder + Telegram
        # overflow path is still owned by the first POST (which holds
        # the future in ``self._pending``); the duplicate just rides
        # along, with its own copy of the slow-LLM placeholder for the
        # subset of cases where Even App picks the duplicate's response
        # *before* the first POST gets there.
        existing = self._pending.get(chat_id)
        if existing is not None and not existing.done():
            logger.info(
                "[EvenAI] hedged duplicate POST for %s — sharing in-flight future",
                chat_id,
            )
            try:
                content = await asyncio.wait_for(
                    asyncio.shield(existing), timeout=self.response_timeout,
                )
            except asyncio.TimeoutError:
                content = self.slow_placeholder
                logger.info(
                    "[EvenAI] %s: hedged POST exceeded %ss — placeholder shipped on duplicate",
                    chat_id, self.response_timeout,
                )
            formatted = _truncate_for_g2(content, self.max_chars)
            return web.json_response(self._build_completion(body, formatted))

        # Build the Hermes MessageEvent (only for the FIRST POST in a
        # logical turn — retries above never reach this point).
        source = self.build_source(
            chat_id=chat_id,
            chat_name=chat_name,
            chat_type="dm",
            user_id=agent_id,
            user_name=agent_id,
        )
        event = MessageEvent(
            text=user_text,
            message_type=MessageType.TEXT,
            source=source,
            message_id=f"even-ai-{uuid.uuid4().hex}",
        )

        # Install the pending future BEFORE dispatching so ``send`` can
        # resolve it inline.
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[chat_id] = future

        # Dispatch to the gateway runner. ``handle_message`` is async but
        # non-blocking from the platform side once enqueued.
        try:
            await self.handle_message(event)
        except Exception as exc:
            logger.exception("[EvenAI] handle_message raised: %s", exc)
            self._pending.pop(chat_id, None)
            return web.json_response(
                {"error": f"agent dispatch failed: {exc}"}, status=500,
            )

        # Await the agent reply within our slow-LLM threshold.  Two
        # outcomes:
        #   * future resolves in time → ship the real reply on this POST.
        #   * 22s budget elapses → ship a placeholder on this POST, and
        #     spawn a background task that waits for the future and
        #     re-delivers the eventual reply through the Telegram
        #     fallback (Phase 4+5 二段構成).
        overflow = False
        try:
            content = await asyncio.wait_for(
                asyncio.shield(future), timeout=self.response_timeout,
            )
        except asyncio.TimeoutError:
            overflow = True
            content = self.slow_placeholder
            logger.info(
                "[EvenAI] %s: agent reply exceeded %ss — placeholder shipped, overflow→Telegram",
                chat_id, self.response_timeout,
            )

        if overflow:
            # Hand the still-pending future over to a background task so
            # the eventual real reply lands in the Telegram fallback.
            self._spawn_overflow_delivery(chat_id, future)
        else:
            # Reply delivered inline — clear the slot.
            self._pending.pop(chat_id, None)

        formatted = _truncate_for_g2(content, self.max_chars)
        return web.json_response(self._build_completion(body, formatted))

    # ── DEBUG ECHO MODE ──────────────────────────────────────────────────

    async def _debug_echo_handler(
        self,
        request: web.Request,
        body: Dict[str, Any],
        chat_id: str,
        user_text: str,
    ) -> web.Response:
        """Sleep ``debug_echo_delay`` seconds and return a fixed echo.

        Instrumented to measure Even AI's client-side behavior:
          * logs POST arrival with timestamp
          * detects retry POST during the wait (via ``self._pending``)
          * polls the underlying transport to detect client disconnect
            (the Even App closes the socket when its client deadline
            trips, even though the server is still computing a reply)
          * logs the disconnect timestamp so we can correlate against
            the configured delay
        """
        loop = asyncio.get_running_loop()
        delay = self.debug_echo_delay
        arrival = loop.time()
        is_retry = False
        existing = self._pending.get(chat_id)
        if existing is not None and not existing.done():
            is_retry = True
            logger.info(
                "[EvenAI-debug] hedged duplicate POST for %s mid-delay — sharing in-flight future",
                chat_id,
            )
            try:
                # Cap the wait at delay + 5s so a stuck future doesn't
                # hold the duplicate connection open indefinitely.
                shared = await asyncio.wait_for(
                    asyncio.shield(existing), timeout=delay + 5.0,
                )
                payload = (
                    f"(debug, hedged duplicate) shared content: {shared[:120]}"
                )
            except asyncio.TimeoutError:
                payload = (
                    "(debug, hedged duplicate) timed out waiting for primary future"
                )
            return web.json_response(self._build_completion(body, payload))

        # Mark this chat as in-flight so a subsequent retry POST hits
        # the retry branch above.
        future: asyncio.Future = loop.create_future()
        self._pending[chat_id] = future
        logger.info(
            "[EvenAI-debug] POST arrived: chat=%s text=%r delay=%.1fs — sleeping",
            chat_id, user_text[:40], delay,
        )

        # Poll connection state every 0.5s.  The aiohttp request's
        # transport reports ``is_closing()`` when the peer shuts the
        # socket — i.e. when the Even App gives up on this POST.
        disconnect_t: Optional[float] = None
        try:
            elapsed = 0.0
            tick = 0.5
            while elapsed < delay:
                await asyncio.sleep(min(tick, delay - elapsed))
                elapsed = loop.time() - arrival
                transport = request.transport
                if transport is not None and transport.is_closing():
                    if disconnect_t is None:
                        disconnect_t = elapsed
                        logger.info(
                            "[EvenAI-debug] %s: client disconnected at t=%.1fs (delay budget %.1fs)",
                            chat_id, elapsed, delay,
                        )
        finally:
            self._pending.pop(chat_id, None)
            if not future.done():
                future.set_result("(debug) wait complete")

        total = loop.time() - arrival
        disc_str = f"{disconnect_t:.1f}s" if disconnect_t is not None else "no"
        logger.info(
            "[EvenAI-debug] %s: replying after %.1fs (disconnect=%s, retry_seen=%s)",
            chat_id, total, disc_str, is_retry,
        )

        reply_text = (
            f"(debug) {delay:.0f}秒待って返したよ。disconnect={disc_str} "
            f"retry_seen={is_retry} 元発話=「{user_text[:30]}」"
        )
        return web.json_response(self._build_completion(body, reply_text))

    # ── Overflow delivery (slow-LLM → Telegram fallback) ─────────────────

    def _spawn_overflow_delivery(
        self, chat_id: str, future: "asyncio.Future[str]",
    ) -> None:
        """Schedule a background task that waits for ``future`` and pushes
        the eventual reply to Telegram so the user still receives it.

        Caller has already returned a placeholder on the inbound POST.
        """
        loop = asyncio.get_running_loop()
        task = loop.create_task(self._deliver_overflow(chat_id, future))
        self._overflow_tasks.add(task)
        task.add_done_callback(self._overflow_tasks.discard)

    async def _deliver_overflow(
        self, chat_id: str, future: "asyncio.Future[str]",
    ) -> None:
        try:
            # Even AI's own server-side deadline is ~30s; the gateway
            # runner may continue for several minutes for slow tool
            # chains.  Cap the wait so a stuck future doesn't leak the
            # task forever.
            content = await asyncio.wait_for(future, timeout=600.0)
        except asyncio.TimeoutError:
            logger.warning(
                "[EvenAI] overflow delivery for %s timed out after 600s; dropping",
                chat_id,
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "[EvenAI] overflow delivery for %s failed: %s",
                chat_id, exc,
            )
            return
        finally:
            self._pending.pop(chat_id, None)

        if not content or not content.strip():
            return

        # Truncate first so the Telegram message stays comparable to
        # what the HUD would have shown — Telegram itself has a 4096-char
        # cap, our cap of 400 keeps both consistent.
        body = _truncate_for_g2(content, self.max_chars)
        await self._push_to_telegram_fallback(
            body, reason=f"slow-reply for {chat_id}",
        )

    @staticmethod
    def _build_completion(request_body: Dict[str, Any], content: str) -> Dict[str, Any]:
        return {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": str(request_body.get("model") or "openclaw"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
        }

    # ── Outbound ──────────────────────────────────────────────────────────

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        future = self._pending.get(chat_id)
        if future is not None and not future.done():
            future.set_result(content)
            return SendResult(
                success=True,
                message_id=f"even-ai-{uuid.uuid4().hex[:12]}",
            )
        # No pending POST → proactive send.  Even AI is request/response
        # only (no server push channel), so route the message into the
        # Telegram fallback so the user still receives it.
        body = _truncate_for_g2(content, self.max_chars)
        delivered = await self._push_to_telegram_fallback(
            body, reason=f"proactive→{chat_id}",
        )
        if delivered:
            return SendResult(
                success=True,
                message_id=f"even-ai-tg-{uuid.uuid4().hex[:12]}",
            )
        return SendResult(
            success=False,
            error=(
                "Even AI proactive send failed: Telegram fallback "
                "unavailable (set EVEN_AI_TELEGRAM_FALLBACK=true and "
                "TELEGRAM_HOME_CHANNEL or EVEN_AI_TELEGRAM_CHAT_ID)."
            ),
        )

    # ── Telegram fallback push ────────────────────────────────────────────

    async def _push_to_telegram_fallback(
        self, content: str, *, reason: str = "",
    ) -> bool:
        """Send ``content`` to the configured Telegram chat.

        Tries the live in-process Telegram adapter first (via the
        gateway runner weakref).  If that path is unavailable — which
        happens when the gateway runner is not in this process, e.g.
        ``hermes cron`` invoking ``standalone_sender_fn`` — falls back
        to ``tools.send_message_tool._send_telegram`` which calls the
        Telegram Bot API directly with ``TELEGRAM_BOT_TOKEN``.

        Returns True on success, False otherwise (with the reason
        logged).  Never raises.
        """
        if not self.telegram_fallback_enabled:
            logger.info(
                "[EvenAI] telegram fallback disabled — dropping %s (%d chars)",
                reason or "send", len(content),
            )
            return False
        if not self.telegram_fallback_chat_id:
            logger.warning(
                "[EvenAI] telegram fallback chat_id not configured — dropping %s",
                reason or "send",
            )
            return False

        payload = content
        if self.telegram_fallback_prefix:
            payload = f"{self.telegram_fallback_prefix}{payload}"

        chat_id = self.telegram_fallback_chat_id

        # Attempt 1: live in-process Telegram adapter.
        try:
            from gateway.run import _gateway_runner_ref  # type: ignore
        except Exception:
            runner_ref = None
        else:
            try:
                runner_ref = _gateway_runner_ref()
            except Exception:
                runner_ref = None

        if runner_ref is not None:
            try:
                tg_adapter = runner_ref.adapters.get(Platform("telegram"))
            except Exception:
                tg_adapter = None
            if tg_adapter is not None:
                try:
                    result = await tg_adapter.send(
                        chat_id=chat_id, content=payload,
                    )
                except Exception as exc:
                    logger.warning(
                        "[EvenAI] live Telegram adapter send failed (%s): %s",
                        reason, exc,
                    )
                else:
                    if getattr(result, "success", False):
                        logger.info(
                            "[EvenAI] %s delivered via live Telegram adapter",
                            reason or "send",
                        )
                        return True
                    logger.warning(
                        "[EvenAI] live Telegram adapter returned failure (%s): %s",
                        reason, getattr(result, "error", "(no error)"),
                    )

        # Attempt 2: direct Telegram Bot API call.
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            logger.warning(
                "[EvenAI] no TELEGRAM_BOT_TOKEN for fallback — dropping %s",
                reason or "send",
            )
            return False
        try:
            from tools.send_message_tool import _send_telegram  # type: ignore
        except Exception as exc:
            logger.warning(
                "[EvenAI] cannot import tools._send_telegram (%s): %s",
                reason, exc,
            )
            return False
        try:
            result = await _send_telegram(
                token=token, chat_id=chat_id, message=payload,
            )
        except Exception as exc:
            logger.warning(
                "[EvenAI] direct Telegram send raised (%s): %s",
                reason, exc,
            )
            return False
        if isinstance(result, dict) and result.get("success"):
            logger.info(
                "[EvenAI] %s delivered via direct Telegram API",
                reason or "send",
            )
            return True
        logger.warning(
            "[EvenAI] direct Telegram send failed (%s): %r",
            reason, result,
        )
        return False

    async def send_typing(self, chat_id: str, metadata: Any = None) -> None:
        """Even AI has no typing indicator — no-op."""
        return None

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        return {"name": chat_id, "type": "dm"}


def _constant_time_equals(a: str, b: str) -> bool:
    """Constant-time string comparison to avoid token timing leaks."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a.encode("utf-8"), b.encode("utf-8")):
        result |= x ^ y
    return result == 0


def _truthy(value: Optional[str]) -> bool:
    """Common-sense parsing of bool-like env vars."""
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on", "t", "y"}


async def _standalone_send(
    pconfig,
    chat_id: str,
    message: str,
    *,
    thread_id: Optional[str] = None,
    media_files: Optional[List[str]] = None,
    force_document: bool = False,
) -> Dict[str, Any]:
    """Out-of-process delivery for ``deliver=even-ai`` cron jobs.

    Even AI has no server-push channel — the only way the user receives
    a message is by polling via the iPhone Even App, which we can't
    initiate.  So ``deliver=even-ai`` cron jobs route through the
    Telegram fallback instead, the same way slow-reply overflow does
    in the live path.

    ``thread_id``, ``media_files``, ``force_document`` are accepted for
    signature parity with the dispatcher but ignored — Telegram threads
    and Telegram-specific media flow through ``tools/send_message_tool``
    directly when callers need those features.
    """
    extra = getattr(pconfig, "extra", {}) or {}

    if not _truthy(os.getenv("EVEN_AI_TELEGRAM_FALLBACK", "true")):
        return {
            "error": (
                "Even AI standalone send: telegram fallback disabled "
                "(EVEN_AI_TELEGRAM_FALLBACK=false). Even AI has no "
                "push channel of its own."
            ),
        }

    tg_chat = (
        os.getenv("EVEN_AI_TELEGRAM_CHAT_ID")
        or os.getenv("TELEGRAM_HOME_CHANNEL")
        or extra.get("telegram_fallback_chat_id")
        or ""
    ).strip()
    if not tg_chat:
        return {
            "error": (
                "Even AI standalone send: no Telegram fallback chat_id "
                "configured (set EVEN_AI_TELEGRAM_CHAT_ID or "
                "TELEGRAM_HOME_CHANNEL)."
            ),
        }

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return {
            "error": (
                "Even AI standalone send: TELEGRAM_BOT_TOKEN missing — "
                "cannot deliver via Telegram fallback."
            ),
        }

    prefix = os.getenv("EVEN_AI_TELEGRAM_FALLBACK_PREFIX", "")
    body = _truncate_for_g2(
        message,
        int(os.getenv("EVEN_AI_MAX_CHARS", "") or DEFAULT_MAX_CHARS),
    )
    payload = f"{prefix}{body}" if prefix else body

    try:
        from tools.send_message_tool import _send_telegram  # type: ignore
    except Exception as exc:
        return {
            "error": f"Even AI standalone send: cannot import _send_telegram: {exc}",
        }

    try:
        result = await _send_telegram(
            token=token, chat_id=tg_chat, message=payload,
        )
    except Exception as exc:
        return {"error": f"Even AI standalone send: Telegram API call raised: {exc}"}

    if isinstance(result, dict) and result.get("success"):
        return result
    if isinstance(result, dict) and result.get("error"):
        return result
    return {
        "error": (
            f"Even AI standalone send: unexpected Telegram response: {result!r}"
        ),
    }


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


def check_requirements() -> bool:
    """Return True when the plugin can run: aiohttp installed + token set."""
    return AIOHTTP_AVAILABLE and bool(os.getenv("EVEN_AI_AUTH_TOKEN", "").strip())


def validate_config(config: PlatformConfig) -> bool:
    extra = getattr(config, "extra", {}) or {}
    token = os.getenv("EVEN_AI_AUTH_TOKEN") or extra.get("token", "")
    return bool(token) and AIOHTTP_AVAILABLE


def is_connected(config: PlatformConfig) -> bool:
    return validate_config(config)


def _env_enablement() -> Optional[Dict[str, Any]]:
    """Seed PlatformConfig.extra from env vars for env-only setups."""
    token = os.getenv("EVEN_AI_AUTH_TOKEN", "").strip()
    if not token:
        return None
    seed: Dict[str, Any] = {"token": token}
    host = os.getenv("EVEN_AI_BIND_HOST", "").strip()
    if host:
        seed["bind_host"] = host
    port = os.getenv("EVEN_AI_BIND_PORT", "").strip()
    if port:
        try:
            seed["bind_port"] = int(port)
        except ValueError:
            pass
    max_chars = os.getenv("EVEN_AI_MAX_CHARS", "").strip()
    if max_chars:
        try:
            seed["max_chars"] = int(max_chars)
        except ValueError:
            pass
    timeout = os.getenv("EVEN_AI_RESPONSE_TIMEOUT", "").strip()
    if timeout:
        try:
            seed["response_timeout"] = float(timeout)
        except ValueError:
            pass
    home = os.getenv("EVEN_AI_HOME_CHAT_ID", "").strip()
    if home:
        seed["home_channel"] = {
            "chat_id": home,
            "name": os.getenv("EVEN_AI_HOME_CHAT_NAME", "Even AI Home"),
        }
    return seed


def register(ctx: Any) -> None:
    """Plugin entry point — called by the Hermes plugin system."""
    ctx.register_platform(
        name="even-ai",
        label="Even AI",
        adapter_factory=lambda cfg: EvenAIAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        is_connected=is_connected,
        required_env=["EVEN_AI_AUTH_TOKEN"],
        install_hint="pip install aiohttp (usually already present in Hermes)",
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="EVEN_AI_HOME_CHAT_ID",
        standalone_sender_fn=_standalone_send,
        allowed_users_env="EVEN_AI_ALLOWED_AGENT_IDS",
        allow_all_env="EVEN_AI_ALLOW_ALL_USERS",
        max_message_length=DEFAULT_MAX_CHARS,
        emoji="👓",
        pii_safe=False,
        allow_update_command=True,
        platform_hint=(
            "You are responding via Even Realities G2 smart glasses HUD. "
            "Output is shown on a 576x136 monochrome display. "
            "Keep responses under 400 characters, use plain text only "
            "(no markdown, no code blocks, no URLs), and prefer a single "
            "short paragraph. Line breaks are honored on the HUD so a few "
            "sentences with explicit \\n between them are fine."
        ),
    )
