"""F-11 / P5-01: session-signing and runtime dependencies stay on the reviewed lock."""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_itsdangerous_is_pinned_exactly_in_pyproject():
    text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"itsdangerous==2.2.0"' in text
    assert "itsdangerous>=" not in text


def test_lockfile_pins_itsdangerous_2_2_0():
    lines = [
        line.strip()
        for line in (_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert "itsdangerous==2.2.0" in lines


def test_lock_entry_point_only_includes_the_pinned_file():
    # Dependabot cannot read requirements.lock, so the pins must stay in
    # requirements.txt. Regenerating the wrong file would overwrite this include.
    lines = [
        line.strip()
        for line in (_ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == ["-r requirements.txt"]


def test_ci_installs_from_the_lockfile_not_open_ranges():
    text = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "pip install -r requirements.lock" in text
    assert "pip install -e . --no-deps" in text
    assert "pip install -e .[test]" not in text
    assert "pip_audit -r requirements.txt" in text


def test_start_bat_uses_the_lockfile_and_no_deps():
    text = (_ROOT / "start.bat").read_text(encoding="utf-8")
    assert "pip install -q -r requirements.lock" in text
    assert "pip install -q -e . --no-deps" in text
    assert "requirements.txt" not in text


def test_installed_itsdangerous_is_the_pinned_session_version():
    import importlib.metadata as metadata
    from itsdangerous import URLSafeTimedSerializer
    import inspect

    assert metadata.version("itsdangerous") == "2.2.0"
    params = inspect.signature(URLSafeTimedSerializer.loads).parameters
    assert "return_timestamp" in params
    assert "max_age" in params


# Pins that were in the Phase-4 lockfile (commit 2a49819) and require
# Python >=3.12. CI runs 3.10 and 3.11; pyproject declares >=3.10. A lockfile
# containing these cannot be installed in CI or on Ubuntu 22.04.
_PHASE4_PYTHON312_ONLY_PINS = (
    "numpy==2.5.3",
    "scipy==1.18.1",
    "pandas==3.0.5",
)


def test_lockfile_does_not_pin_phase4_python312_only_wheels():
    text = (_ROOT / "requirements.txt").read_text(encoding="utf-8")
    for pin in _PHASE4_PYTHON312_ONLY_PINS:
        assert pin not in text, (
            f"{pin} requires Python >=3.12 and cannot be installed on CI 3.11"
        )


def test_pyproject_bounds_keep_the_declared_python_matrix():
    text = (_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'requires-python = ">=3.10"' in text
    assert "numpy>=1.26,<2.3" in text
    assert "pandas>=2.0,<3" in text
    assert "scipy>=1.11,<1.16" in text
    assert "websockets>=13,<17" in text


def test_lockfile_websockets_supports_python_3_10():
    lines = [
        line.strip()
        for line in (_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    ws = [line for line in lines if line.startswith("websockets==")]
    assert len(ws) == 1
    version = ws[0].split("==", 1)[1]
    major = int(version.split(".", 1)[0])
    assert major < 17, f"{ws[0]} requires Python >=3.11"


def test_ci_python_is_inside_the_declared_matrix():
    text = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    # Production's platform has to be in the matrix: Python 3.12 on aarch64.
    assert "{ os: ubuntu-24.04-arm, python-version: '3.12' }" in text
    assert "python-version: ${{ matrix.python-version }}" in text


def test_lockfile_pins_python310_conditional_runtime_and_test_deps():
    """exceptiongroup is a runtime dep of anyio on 3.10; tomli is pytest's."""
    lines = [
        line.strip()
        for line in (_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    # Only the marker is the contract; Dependabot moves the versions.
    for name in ("exceptiongroup", "tomli"):
        assert any(
            line.startswith(f"{name}==") and line.endswith('; python_version < "3.11"')
            for line in lines
        ), name


def test_python310_lock_install_gets_the_conditional_pins():
    """exceptiongroup is a runtime dep of anyio on 3.10; tomli is pytest's.
    On 3.11 they are not required by the lockfile (marker-gated). CI also
    installs pip-audit, which itself depends on tomli on every Python, so
    absence on 3.11 is not a stable assertion."""
    import importlib.metadata as metadata
    import sys

    if sys.version_info >= (3, 11):
        return
    pins = dict(
        line.split(";", 1)[0].strip().split("==", 1)
        for line in (_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.startswith(("exceptiongroup==", "tomli=="))
    )
    assert metadata.version("exceptiongroup") == pins["exceptiongroup"]
    assert metadata.version("tomli") == pins["tomli"]


def test_locked_scientific_stack_declares_support_for_ci_and_minimum_python():
    """Installed distributions (from requirements.lock) must accept 3.10 and 3.11."""
    import importlib.metadata as metadata
    from packaging.specifiers import SpecifierSet

    for name in ("numpy", "scipy", "pandas"):
        requires = metadata.metadata(name).get("Requires-Python") or ""
        spec = SpecifierSet(requires)
        assert spec.contains("3.10", prereleases=True), f"{name} Requires-Python={requires!r} rejects 3.10"
        assert spec.contains("3.11", prereleases=True), f"{name} Requires-Python={requires!r} rejects 3.11"
        assert spec.contains("3.12", prereleases=True), f"{name} Requires-Python={requires!r} rejects 3.12"
