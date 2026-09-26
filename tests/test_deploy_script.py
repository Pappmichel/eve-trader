"""deploy/deploy.sh regression guard (2026-09-26): a freshly-changed schema
file lands on disk with whatever mode `git pull`'s checkout used, which
inherits the *invoking interactive shell's* umask, not this script's own -
under the `umask 077` hardening fix from T1-05, that came out 0600 (owner-
only), unreadable by the `postgres` OS user the schema-apply loop runs as.
Confirmed live: a real deploy aborted on exactly this ("Permission denied"
reading docs/esi_access_schema.sql) the same day this test was added. No
VM/Postgres available here to exercise the script itself - this just
grep-checks the fix is present and ordered correctly, same style as
test_gate_migration.py's own deploy.sh text assertions."""
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_schema_files_are_chmod_readable_before_the_apply_loop():
    deploy = (_ROOT / "deploy" / "deploy.sh").read_text(encoding="utf-8")

    chmod_pos = deploy.find("chmod 644 docs/*.sql")
    loop_pos = deploy.find("for f in phase1_schema.sql")

    assert chmod_pos != -1, "deploy.sh must chmod docs/*.sql readable, not depend on the caller's umask"
    assert loop_pos != -1, "deploy.sh's schema-apply loop is missing entirely"
    assert chmod_pos < loop_pos, "the chmod must run before the schema-apply loop, not after"
