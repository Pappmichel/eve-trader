# Versioning (online)

Package version `0.3.0` (PEP 440, `pyproject.toml` and
`eve_trader.__version__`). It is bumped by hand when something worth naming
ships; it is not a release train. The Admin health detail
(`GET /api/admin/health`) reports it.

There are **no release tags** in this repository. `0.2.0rc1` was described
as a release candidate with a tag `v0.2.0-rc1` in earlier versions of this
file and `docs/archive/PHASE_H_RELEASE.md`; that tag never existed. Do not
assume it exists on any clone or remote.

What identifies a running deployment instead: the commit. `deploy/deploy.sh`
records the previous and the new commit under `data/deploy-state/` and, after
a successful deploy, creates a **local** tag `deploy-<UTC timestamp>` on the
deployed commit in the server's clone. Those tags are a trail of what ran on
that machine; they are not pushed (the deploy key is read-only), are not
releases, and do not exist in this repository's history. `git tag -l
'deploy-*'` on the server lists them.

The previous package versions were `0.1.0` and `0.2.0rc1` (the Special-Order
path certified after Phases C–G; not a claim that Trading/Doctrine/Ore are
frozen).

The local desktop tool uses a separate identifier (`0.4.0rc1`).
