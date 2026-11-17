"""CLAUDE.md's tool-key table must name every key in access_gate.ALL_TOOL_KEYS."""
from pathlib import Path

from eve_trader.access_gate import ALL_TOOL_KEYS

_ROOT = Path(__file__).resolve().parent.parent


def test_every_tool_key_is_in_backticks_in_claude_md():
    text = (_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    missing = [key for key in ALL_TOOL_KEYS if f"`{key}`" not in text]
    assert not missing, f"CLAUDE.md does not list these tool keys in backticks: {missing}"
