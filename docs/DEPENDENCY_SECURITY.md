# Dependency security

Runtime and test installs are reproducible. CI and deploy install
`requirements.lock`, then the editable package with `--no-deps`, so
pyproject.toml ranges cannot silently pull a newer itsdangerous (or
anything else that session signing depends on).

## Python support matrix

`pyproject.toml` declares `requires-python = ">=3.10"`. CI
(`.github/workflows/ci.yml`) runs a matrix of **Python 3.10 and 3.11**.
Deploy (`deploy/setup.sh`) uses the VM's `python3` (Ubuntu 22.04 = 3.10,
24.04 = 3.12). The lockfile must install on **all three**.

Upper bounds on `numpy` (`<2.3`), `scipy` (`<1.16`), `pandas`
(`<3`), and `websockets` (`<17`) exist so a lockfile regenerated on
Python 3.12 cannot pin wheels that refuse 3.10/3.11 (P5-01: `numpy==2.5.3`,
`scipy==1.18.1`, and `pandas==3.0.5` require Python >=3.12;
`websockets==17.1` requires Python >=3.11).

Python 3.10 needs two extra packages that 3.11+ ships in the stdlib (or
does not need). They are pinned in `requirements.lock` with environment
markers so 3.11/3.12 installs do not pull them:

- `exceptiongroup==1.3.1; python_version < "3.11"` — runtime: `anyio`
  (Starlette → FastAPI) requires it below 3.11.
- `tomli==2.4.1; python_version < "3.11"` — pytest's TOML reader below 3.11.

A lockfile regenerated with `pip freeze` on 3.11 will omit both; copy the
marker lines back from the previous lock before committing.

## Pins

- `pyproject.toml` declares compatible ranges plus an **exact** pin for
  `itsdangerous==2.2.0` (session cookies:
  `URLSafeTimedSerializer.loads(..., return_timestamp=True)`).
- `requirements.lock` pins every runtime and test package to the versions
  last reviewed together. That is what production and CI install.

## Install (dev, CI, deploy, Windows `start.bat`)

```bash
pip install -r requirements.lock
pip install -e . --no-deps
```

Do not `pip install -e .[test]` on a gated deployment: that re-resolves
ranges and can move session-signing code. `start.bat` is the Windows
local-dev double-click helper; it uses this same lock strategy.

`requirements.txt` is a compatibility shim (`-r requirements.lock`) for
tools that still point at that filename. Prefer `requirements.lock`
directly.

Regenerate the lockfile **on Python 3.11** (one of the two CI versions),
then confirm a clean 3.10 venv can also `pip install -r requirements.lock`
and restores the `exceptiongroup` / `tomli` marker pins.

## Vulnerability scanning

CI runs `pip-audit -r requirements.lock` on every push/PR (backend job). A
finding is a failed check, not a silent log line. The frontend job mirrors
this with `npm audit --omit dev` against `frontend/package-lock.json` (F-06,
business-logic audit follow-up, 2026-09-26) - `--omit dev` matches
production's own `npm ci` (no dev-only tooling ships to a real user, so a
vulnerable linter/test-runner doesn't need to fail this build the same way a
vulnerable shipped dependency would).

`.github/workflows/ci.yml`'s own `actions/checkout`/`actions/setup-python`/
`actions/setup-node` steps are SHA-pinned, not on a moving version tag (F-06)
- a compromised/re-tagged release would otherwise run in CI, with repo
secrets in scope, without this workflow file itself ever changing.
Dependabot's existing `github-actions` ecosystem entry (`.github/
dependabot.yml`) already knows how to open a PR re-pinning to a new SHA when
a tag moves - nothing extra to configure there.

## Updating a dependency

1. Review the changelog (for `itsdangerous`, re-read
   `access_gate.read_session_token` / `return_timestamp` behaviour).
2. Install the new version in a venv, run `pytest`, run `pip-audit`.
3. Rewrite `requirements.lock` from `pip freeze` (see the header comment in
   that file). Keep the `itsdangerous==` line in `pyproject.toml` in sync.
   Restore the Python 3.10 environment-marker pins if freeze dropped them.
4. Open a PR. Dependabot PRs follow the same review; they are not
   auto-merged.

Avoid unreviewed major-version upgrades.
