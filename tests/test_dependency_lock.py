"""F-11: session-signing and runtime dependencies stay on the reviewed lock."""
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_itsdangerous_is_pinned_exactly_in_pyproject():
    text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"itsdangerous==2.2.0"' in text
    assert "itsdangerous>=" not in text


def test_lockfile_pins_itsdangerous_2_2_0():
    lines = [
        line.strip()
        for line in (_ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert "itsdangerous==2.2.0" in lines


def test_ci_installs_from_the_lockfile_not_open_ranges():
    text = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "pip install -r requirements.lock" in text
    assert "pip install -e . --no-deps" in text
    assert "pip install -e .[test]" not in text
    assert "pip_audit -r requirements.lock" in text


def test_installed_itsdangerous_is_the_pinned_session_version():
    import importlib.metadata as metadata
    from itsdangerous import URLSafeTimedSerializer
    import inspect

    assert metadata.version("itsdangerous") == "2.2.0"
    params = inspect.signature(URLSafeTimedSerializer.loads).parameters
    assert "return_timestamp" in params
    assert "max_age" in params
