"""deploy/deploy.sh: static rules plus the git-level steps in a throwaway
repository (local "origin" in tmp_path). The systemd/sudo/psql/npm parts
cannot run here; the script exposes its steps as functions and only runs a
deploy when not sourced with DEPLOY_SOURCE_ONLY=1."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "deploy" / "deploy.sh"


def _code_lines() -> list[str]:
    """Script lines without comment-only lines (comments may mention the
    forbidden commands to explain why they are forbidden)."""
    return [
        line for line in SCRIPT.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    ]


# --- static -----------------------------------------------------------------


def test_no_plain_git_pull():
    assert not [line for line in _code_lines() if re.search(r"\bgit\s+pull\b", line)]


def test_every_merge_is_fast_forward_only():
    merges = [line for line in _code_lines() if re.search(r"\bgit\s+merge\b", line)]
    assert merges
    assert all("--ff-only" in line for line in merges)


def test_config_yaml_is_never_edited_in_place():
    for line in _code_lines():
        assert not ("sed -i" in line and "config.yaml" in line), line
    assert not [line for line in _code_lines() if re.search(r"\bsed\s+(-\w*i|--in-place)", line)]
    assert not [line for line in _code_lines() if re.search(r"\b(chmod|chown)\b.*(config\.yaml|\.env|data/)", line)]


def test_chmod_is_limited_to_schema_files():
    chmods = [line.strip() for line in _code_lines() if re.search(r"\bchmod\b", line)]
    assert chmods and all(line == "chmod 644 docs/*.sql" for line in chmods)


def test_frontend_builds_into_dist_new_and_is_swapped():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "npm run build -- --outDir dist.new --emptyOutDir" in text
    assert "mv dist dist.prev" in text and "mv dist.new dist" in text
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "frontend/dist.prev/" in gitignore and "frontend/dist.new/" in gitignore


def test_health_check_polls_the_health_endpoint_and_checks_the_body():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "curl -fsS --retry 15 --retry-delay 2 --retry-connrefused" in text
    assert "/api/health" in text
    assert '"status":"ok"' in text
    assert "systemctl is-active" in text


def test_backup_runs_before_the_schema_step_through_the_cli_and_failure_aborts():
    text = SCRIPT.read_text(encoding="utf-8")
    backup_at = text.index("backup create --pre-deploy")
    schema_at = text.index('psql -d eve_trader -1 -v ON_ERROR_STOP=1 "${schema_args[@]}"')
    assert backup_at < schema_at
    code = "\n".join(_code_lines())
    assert "pg_dump" not in code
    assert "/tmp" not in code
    assert "pre-deploy backup failed" in code


def test_reexec_guard_comes_after_fetch_and_merge():
    text = SCRIPT.read_text(encoding="utf-8")
    assert text.index("deploy_update_code\n") < text.index('exec "$APP_DIR/deploy/deploy.sh"')
    assert "EVE_TRADER_DEPLOY_REEXECED" in text


def test_schema_loop_keeps_the_shape_the_test_database_reads():
    from tests import pg_helpers

    files = pg_helpers._deploy_schema_files()
    assert files[0].name == "phase1_schema.sql"
    assert all(path.exists() for path in files)


def test_script_has_valid_bash_syntax():
    bash = _bash()
    if bash is None:
        pytest.skip("no working bash")
    res = subprocess.run([bash, "-n", str(SCRIPT)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_version_is_030():
    assert 'version = "0.3.0"' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    from eve_trader import __version__

    assert __version__ == "0.3.0"


# --- git level, throwaway repository ---------------------------------------


def _bash():
    found = shutil.which("bash")
    if not found:
        return None
    try:
        out = subprocess.run([found, "-c", "echo ok"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return found if out.returncode == 0 and out.stdout.strip() == "ok" else None


BASH = _bash()
needs_bash = pytest.mark.skipif(BASH is None, reason="no working bash")

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull,
}


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, **_GIT_ENV}
    res = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True)
    return res.stdout.strip()


def _commit(repo: Path, name: str, content: str) -> str:
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(content, encoding="utf-8", newline="\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", f"change {name}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    """origin (bare) + a working clone that carries deploy/deploy.sh."""
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    (seed / "deploy").mkdir()
    shutil.copy(SCRIPT, seed / "deploy" / "deploy.sh")
    (seed / ".gitignore").write_text("data/\nfrontend/dist.prev/\nconfig.yaml\n.env\n", encoding="utf-8")
    first = _commit(seed, "docs/a_schema.sql", "-- one\n")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "--bare", str(seed), str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "clone", str(origin), str(work))
    return {"seed": seed, "origin": origin, "work": work, "first": first}


def _run(work: Path, snippet: str, **env) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **_GIT_ENV, "DEPLOY_SOURCE_ONLY": "1", **env}
    return subprocess.run(
        [BASH, "-c", f". deploy/deploy.sh; {snippet}"],
        cwd=work, env=full_env, capture_output=True, text=True, timeout=120,
    )


def _publish(repos, name, content) -> str:
    sha = _commit(repos["seed"], name, content)
    _git(repos["seed"], "push", str(repos["origin"]), "main")
    return sha


@needs_bash
def test_update_fast_forwards_records_state_and_prints_both_shas(repos):
    work = repos["work"]
    new = _publish(repos, "docs/b_schema.sql", "-- two\n")

    res = _run(work, 'deploy_update_code; echo "PREV=$DEPLOY_PREV_SHA NEW=$DEPLOY_NEW_SHA"')

    assert res.returncode == 0, res.stderr
    assert f"PREV={repos['first']} NEW={new}" in res.stdout
    assert _git(work, "rev-parse", "HEAD") == new
    state = work / "data" / "deploy-state"
    assert (state / "previous_sha").read_text().strip() == repos["first"]
    assert (state / "current_sha").read_text().strip() == new
    if sys.platform != "win32":
        assert (state.stat().st_mode & 0o077) == 0


@needs_bash
def test_rerun_without_new_commits_keeps_the_earlier_previous(repos):
    work = repos["work"]
    new = _publish(repos, "docs/b_schema.sql", "-- two\n")
    assert _run(work, "deploy_update_code").returncode == 0
    assert _run(work, "deploy_update_code").returncode == 0
    state = work / "data" / "deploy-state"
    assert (state / "previous_sha").read_text().strip() == repos["first"]
    assert (state / "current_sha").read_text().strip() == new


@needs_bash
def test_modified_tracked_file_aborts_before_anything_changes(repos):
    work = repos["work"]
    _publish(repos, "docs/b_schema.sql", "-- two\n")
    (work / "docs" / "a_schema.sql").write_text("-- local edit\n", encoding="utf-8")

    res = _run(work, "deploy_update_code")

    assert res.returncode != 0
    assert "local changes" in res.stderr
    assert _git(work, "rev-parse", "HEAD") == repos["first"]
    assert not (work / "data" / "deploy-state").exists()


@needs_bash
def test_untracked_files_do_not_block_a_deploy(repos):
    work = repos["work"]
    (work / "config.yaml").write_text("x: 1\n", encoding="utf-8")
    (work / ".env").write_text("A=1\n", encoding="utf-8")
    new = _publish(repos, "docs/b_schema.sql", "-- two\n")
    assert _run(work, "deploy_update_code").returncode == 0
    assert _git(work, "rev-parse", "HEAD") == new


@needs_bash
def test_diverged_local_branch_aborts_without_a_merge_commit(repos):
    work = repos["work"]
    local = _commit(work, "docs/local_schema.sql", "-- local only\n")
    _publish(repos, "docs/b_schema.sql", "-- two\n")

    res = _run(work, "deploy_update_code")

    assert res.returncode != 0
    assert "fast-forward" in res.stderr
    assert _git(work, "rev-parse", "HEAD") == local
    assert _git(work, "rev-list", "--merges", "--count", "HEAD") == "0"
    assert not (work / "data" / "deploy-state" / "current_sha").exists()


@needs_bash
def test_deploy_ref_pins_an_exact_commit(repos):
    work = repos["work"]
    mid = _publish(repos, "docs/b_schema.sql", "-- two\n")
    _publish(repos, "docs/c_schema.sql", "-- three\n")

    res = _run(work, "deploy_update_code", DEPLOY_REF=mid)

    assert res.returncode == 0, res.stderr
    assert _git(work, "rev-parse", "HEAD") == mid


@needs_bash
def test_deploy_ref_behind_the_checkout_is_refused(repos):
    work = repos["work"]
    _publish(repos, "docs/b_schema.sql", "-- two\n")
    assert _run(work, "deploy_update_code").returncode == 0
    head = _git(work, "rev-parse", "HEAD")

    res = _run(work, "deploy_update_code", DEPLOY_REF=repos["first"])

    assert res.returncode != 0
    assert "rollback" in res.stderr
    assert _git(work, "rev-parse", "HEAD") == head


@needs_bash
def test_rollback_checks_out_the_previous_commit_detached_and_deploy_returns_to_the_branch(repos):
    work = repos["work"]
    new = _publish(repos, "docs/b_schema.sql", "-- two\n")
    assert _run(work, "deploy_update_code").returncode == 0

    res = _run(work, 'deploy_rollback_checkout; echo "FROM=$ROLLBACK_FROM TO=$ROLLBACK_TO"')
    assert res.returncode == 0, res.stderr
    assert f"FROM={new} TO={repos['first']}" in res.stdout
    assert _git(work, "rev-parse", "HEAD") == repos["first"]
    assert _git(work, "branch", "--show-current") == ""  # detached
    state = work / "data" / "deploy-state"
    assert (state / "previous_sha").read_text().strip() == new
    assert (state / "current_sha").read_text().strip() == repos["first"]

    # Detached checkout: the next deploy must go back to the branch and forward.
    newer = _publish(repos, "docs/c_schema.sql", "-- three\n")
    res = _run(work, "deploy_update_code")
    assert res.returncode == 0, res.stderr
    assert _git(work, "branch", "--show-current") == "main"
    assert _git(work, "rev-parse", "HEAD") == newer
    assert (state / "previous_sha").read_text().strip() == repos["first"]


@needs_bash
def test_rollback_to_an_explicit_commit_and_unknown_commit(repos):
    work = repos["work"]
    _publish(repos, "docs/b_schema.sql", "-- two\n")
    assert _run(work, "deploy_update_code").returncode == 0

    res = _run(work, f"deploy_rollback_checkout {repos['first']}")
    assert res.returncode == 0, res.stderr
    assert _git(work, "rev-parse", "HEAD") == repos["first"]

    res = _run(work, "deploy_rollback_checkout deadbeefdeadbeef")
    assert res.returncode != 0
    assert "unknown commit" in res.stderr


@needs_bash
def test_rollback_without_a_recorded_previous_deploy_asks_for_a_commit(repos):
    res = _run(repos["work"], "deploy_rollback_checkout")
    assert res.returncode != 0
    assert "no previous deploy" in res.stderr


@needs_bash
def test_rollback_refuses_a_dirty_tree(repos):
    work = repos["work"]
    _publish(repos, "docs/b_schema.sql", "-- two\n")
    assert _run(work, "deploy_update_code").returncode == 0
    head = _git(work, "rev-parse", "HEAD")
    (work / "docs" / "a_schema.sql").write_text("-- local edit\n", encoding="utf-8")

    res = _run(work, "deploy_rollback_checkout")

    assert res.returncode != 0
    assert _git(work, "rev-parse", "HEAD") == head


@needs_bash
def test_schema_change_detection_between_two_commits(repos):
    work = repos["work"]
    same = _publish(repos, "README.md", "not a schema file\n")
    schema = _publish(repos, "docs/b_schema.sql", "-- two\n")
    _git(work, "fetch", "origin")

    assert _run(work, f"deploy_schema_changed {repos['first']} {same}").returncode == 1
    assert _run(work, f"deploy_schema_changed {same} {schema}").returncode == 0
    assert _run(work, f"deploy_schema_changed {schema} {schema}").returncode == 1


@needs_bash
def test_dist_prev_is_trusted_only_for_the_commit_it_was_built_from(repos):
    work = repos["work"]
    (work / "frontend" / "dist.prev").mkdir(parents=True)
    state = work / "data" / "deploy-state"
    state.mkdir(parents=True)
    (state / "dist_prev_sha").write_text("abc123\n", encoding="utf-8")

    assert _run(work, "deploy_dist_prev_matches abc123").returncode == 0
    assert _run(work, "deploy_dist_prev_matches other").returncode == 1


@needs_bash
def test_deploy_stays_on_the_checked_out_branch(repos):
    work = repos["work"]
    _git(repos["seed"], "checkout", "-b", "dev")
    new = _publish_branch(repos, "dev", "docs/d_schema.sql", "-- dev\n")
    _git(work, "fetch", "origin")
    _git(work, "checkout", "-b", "dev", repos["first"])

    res = _run(work, "deploy_update_code")

    assert res.returncode == 0, res.stderr
    assert _git(work, "branch", "--show-current") == "dev"
    assert _git(work, "rev-parse", "HEAD") == new
    assert (work / "data" / "deploy-state" / "branch").read_text().strip() == "dev"


def _publish_branch(repos, branch, name, content) -> str:
    sha = _commit(repos["seed"], name, content)
    _git(repos["seed"], "push", str(repos["origin"]), branch)
    return sha


@needs_bash
def test_deploy_after_rollback_returns_to_the_recorded_branch(repos):
    work = repos["work"]
    _git(repos["seed"], "checkout", "-b", "dev")
    _publish_branch(repos, "dev", "docs/d_schema.sql", "-- dev\n")
    _git(work, "fetch", "origin")
    _git(work, "checkout", "-b", "dev", "origin/dev")
    assert _run(work, "deploy_update_code").returncode == 0
    assert _run(work, "deploy_rollback_checkout").returncode == 0
    assert _git(work, "branch", "--show-current") == ""
    newer = _publish_branch(repos, "dev", "docs/e_schema.sql", "-- more\n")

    res = _run(work, "deploy_update_code")

    assert res.returncode == 0, res.stderr
    assert _git(work, "branch", "--show-current") == "dev"
    assert _git(work, "rev-parse", "HEAD") == newer


@needs_bash
def test_explicit_deploy_branch_wins_over_the_checked_out_one(repos):
    work = repos["work"]
    _git(repos["seed"], "checkout", "-b", "dev")
    _publish_branch(repos, "dev", "docs/d_schema.sql", "-- dev\n")
    _git(work, "fetch", "origin")
    _git(work, "checkout", "-b", "dev", "origin/dev")

    res = _run(work, "deploy_update_code", DEPLOY_BRANCH="main")

    assert res.returncode == 0, res.stderr
    assert _git(work, "branch", "--show-current") == "main"
