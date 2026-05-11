"""Unit tests for Even AI adapter formatting helpers.

Run with::

    python3 -m pytest /Users/shou/songwriting/hermes-plugins/even-ai/test_formatting.py -v

Or standalone::

    python3 /Users/shou/songwriting/hermes-plugins/even-ai/test_formatting.py

No Hermes runtime imports — exercises ``_strip_markdown``,
``_find_natural_cutoff``, and ``_truncate_for_g2`` against the
formatting cases that matter for the G2 HUD.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Load the adapter's private helpers without triggering gateway imports.
#
# ``adapter.py`` does ``from gateway.config import Platform`` at module top —
# that pulls in Hermes runtime config and fails outside a configured Hermes
# environment.  For unit testing the pure helpers, we import the source file
# as a standalone module after stubbing the gateway dependencies.
# ---------------------------------------------------------------------------


def _load_adapter_module():
    adapter_path = Path(__file__).parent / "adapter.py"

    # Minimal stubs so the top-level imports succeed without Hermes installed.
    if "gateway" not in sys.modules:
        gateway_stub = type(sys)("gateway")
        sys.modules["gateway"] = gateway_stub
    if "gateway.config" not in sys.modules:
        config_stub = type(sys)("gateway.config")

        class _Platform(str):
            def __new__(cls, value):
                return str.__new__(cls, value)

        class _PlatformConfig:
            pass

        config_stub.Platform = _Platform
        config_stub.PlatformConfig = _PlatformConfig
        sys.modules["gateway.config"] = config_stub
    if "gateway.platforms" not in sys.modules:
        sys.modules["gateway.platforms"] = type(sys)("gateway.platforms")
    if "gateway.platforms.base" not in sys.modules:
        base_stub = type(sys)("gateway.platforms.base")

        class _BasePlatformAdapter:
            def __init__(self, *args, **kwargs):
                pass

            def _set_fatal_error(self, *a, **kw):
                pass

            def _mark_connected(self):
                pass

            def _mark_disconnected(self):
                pass

            def build_source(self, **kw):
                return kw

            async def handle_message(self, event):
                return None

        class _MessageEvent:
            def __init__(self, **kw):
                self.__dict__.update(kw)

        class _MessageType:
            TEXT = "text"

        class _SendResult:
            def __init__(self, success: bool, message_id: str = "", error: str = ""):
                self.success = success
                self.message_id = message_id
                self.error = error

        base_stub.BasePlatformAdapter = _BasePlatformAdapter
        base_stub.MessageEvent = _MessageEvent
        base_stub.MessageType = _MessageType
        base_stub.SendResult = _SendResult
        sys.modules["gateway.platforms.base"] = base_stub

    spec = importlib.util.spec_from_file_location(
        "even_ai_adapter_for_test", adapter_path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


_adapter = _load_adapter_module()
_strip_markdown = _adapter._strip_markdown
_find_natural_cutoff = _adapter._find_natural_cutoff
_truncate_for_g2 = _adapter._truncate_for_g2


# ---------------------------------------------------------------------------
# _strip_markdown
# ---------------------------------------------------------------------------


def test_strip_bold():
    assert _strip_markdown("**hello** world") == "hello world"
    assert _strip_markdown("__bold__ here") == "bold here"


def test_strip_italic():
    assert _strip_markdown("*italic* foo") == "italic foo"
    # Underscore italic should not eat snake_case identifiers.
    assert _strip_markdown("snake_case_name") == "snake_case_name"


def test_strip_inline_code():
    assert _strip_markdown("run `pytest` now") == "run pytest now"


def test_strip_code_fence():
    src = "```python\nprint('hi')\n```"
    out = _strip_markdown(src)
    assert "```" not in out
    assert "python" not in out  # fence language label dropped
    assert "print('hi')" in out


def test_strip_links():
    assert _strip_markdown("[click](https://example.com)") == "click"
    assert _strip_markdown("see [docs](https://x)") == "see docs"


def test_strip_image():
    assert _strip_markdown("![alt text](pic.png)") == "alt text"


def test_strip_heading():
    assert _strip_markdown("# Title\nbody") == "Title\nbody"
    assert _strip_markdown("### Sub\nfoo") == "Sub\nfoo"


def test_strip_blockquote():
    assert _strip_markdown("> quoted line") == "quoted line"
    assert _strip_markdown(">no space") == "no space"


def test_strip_unordered_list():
    src = "- one\n- two\n- three"
    assert _strip_markdown(src) == "one\ntwo\nthree"
    assert _strip_markdown("* star") == "star"
    assert _strip_markdown("+ plus") == "plus"


def test_strip_ordered_list():
    src = "1. first\n2. second\n10. tenth"
    assert _strip_markdown(src) == "first\nsecond\ntenth"


def test_strip_horizontal_rule():
    src = "before\n---\nafter"
    out = _strip_markdown(src)
    assert "---" not in out
    assert "before" in out and "after" in out


def test_strip_table_separator():
    src = "| a | b |\n|---|---|\n| 1 | 2 |"
    out = _strip_markdown(src)
    # Table separator row must be gone; data rows survive as raw text.
    assert "---" not in out
    assert "| a | b |" in out


def test_strip_idempotent_on_plain_japanese():
    src = "さーき、聞こえてるよ。今日のおしごと、どんな感じ？"
    assert _strip_markdown(src) == src


# ---------------------------------------------------------------------------
# _find_natural_cutoff
# ---------------------------------------------------------------------------


def test_cutoff_prefers_full_stop():
    text = "あいうえお。かきくけこ。さしすせそ"
    # Limit 12 → boundary at index 6 ("。" after かきくけこ" is at 11,
    # the first "。" sits at index 5).  Best cut is just past index 5
    # (so we keep "あいうえお。").
    cut = _find_natural_cutoff(text, 12)
    assert text[:cut] in {"あいうえお。", "あいうえお。かきくけこ。"}
    assert text[:cut].endswith("。")


def test_cutoff_falls_back_to_secondary():
    text = "あいうえお、かきくけこ、さしすせそ"
    cut = _find_natural_cutoff(text, 10)
    assert text[:cut].endswith("、")


def test_cutoff_no_boundary_returns_limit():
    text = "あいうえおかきくけこ"  # No punctuation
    cut = _find_natural_cutoff(text, 5)
    assert cut == 5


def test_cutoff_within_limit_returns_full_length():
    text = "短い文。"
    cut = _find_natural_cutoff(text, 100)
    assert cut == 100  # Helper itself doesn't clamp; _truncate handles short input


# ---------------------------------------------------------------------------
# _truncate_for_g2
# ---------------------------------------------------------------------------


def test_truncate_short_text_unchanged():
    text = "短い応答です。"
    assert _truncate_for_g2(text, 400) == text


def test_truncate_long_text_breaks_at_sentence():
    # Build a paragraph long enough to need truncation.
    sentence = "それはね、たぶんだけど、私は今ここに居ます。"  # 22 chars incl. 。
    text = sentence * 20  # 440 chars
    out = _truncate_for_g2(text, 100)
    assert len(out) <= 100
    # Should land on a sentence terminator — no ellipsis when the cut
    # is on a primary boundary.
    assert out.endswith("。")
    assert "…" not in out


def test_truncate_adds_ellipsis_when_no_boundary():
    text = "あ" * 500
    out = _truncate_for_g2(text, 50)
    assert len(out) == 50
    assert out.endswith("…")


def test_truncate_strips_markdown_first():
    text = "**こんにちは** さき。" + ("ふつうの文。" * 80)
    out = _truncate_for_g2(text, 60)
    assert "**" not in out
    assert "こんにちは" in out


def test_truncate_respects_line_break_boundary():
    text = "一行目です。\n二行目もそこそこ長くて続きます。" + ("追加の文。" * 50)
    out = _truncate_for_g2(text, 30)
    assert len(out) <= 30
    # Newline is a primary terminator — landing on it is OK.
    assert out.endswith(("。", "\n")) or out.endswith("…")


def test_truncate_handles_empty():
    assert _truncate_for_g2("", 400) == ""


def test_truncate_handles_whitespace_only():
    out = _truncate_for_g2("   \n  ", 400)
    assert out == ""


# ---------------------------------------------------------------------------
# Standalone runner — runs without pytest
# ---------------------------------------------------------------------------


def _run_all():
    import inspect

    tests = [
        (name, obj)
        for name, obj in globals().items()
        if name.startswith("test_") and callable(obj)
    ]
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
        except AssertionError as exc:
            failed.append((name, f"AssertionError: {exc}"))
        except Exception as exc:
            failed.append((name, f"{type(exc).__name__}: {exc}"))
        else:
            passed += 1
    total = len(tests)
    print(f"\n{passed}/{total} passed")
    if failed:
        print("\nFailures:")
        for name, msg in failed:
            print(f"  - {name}: {msg}")
        sys.exit(1)


if __name__ == "__main__":
    _run_all()
