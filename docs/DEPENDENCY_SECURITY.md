# Dependency security

Runtime and test installs are reproducible. CI and deploy install
`requirements.lock`, then the editable package with `--no-deps`, so
pyproject.toml ranges cannot silently pull a newer itsdangerous (or
anything else that session signing depends on).

## Pins

- `pyproject.toml` declares compatible ranges plus an **exact** pin for
  `itsdangerous==2.2.0` (session cookies:
  `URLSafeTimedSerializer.loads(..., return_timestamp=True)`).
- `requirements.lock` pins every runtime and test package to the versions
  last reviewed together. That is what production and CI install.

## Install (dev, CI, deploy)

```bash
pip install -r requirements.lock
pip install -e . --no-deps
```

Do not `pip install -e .[test]` on a gated deployment: that re-resolves
ranges and can move session-signing code.

## Vulnerability scanning

CI runs `pip-audit -r requirements.lock` on every push/PR. A finding is a
failed check, not a silent log line.

## Updating a dependency

1. Review the changelog (for `itsdangerous`, re-read
   `access_gate.read_session_token` / `return_timestamp` behaviour).
2. Install the new version in a venv, run `pytest`, run `pip-audit`.
3. Rewrite `requirements.lock` from `pip freeze` (see the header comment in
   that file). Keep the `itsdangerous==` line in `pyproject.toml` in sync.
4. Open a PR. Dependabot PRs follow the same review; they are not
   auto-merged.

Avoid unreviewed major-version upgrades.
