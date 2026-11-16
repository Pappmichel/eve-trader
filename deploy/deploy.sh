#!/usr/bin/env bash
# Bundles the "Updating later" sequence from deploy/README.md into one
# command, run FROM THE VM after the code is already cloned and set up once
# via setup.sh (this script never does initial provisioning - see setup.sh
# for that). Idempotent - every schema file is safe to re-run, matching
# deploy/README.md's own "always re-run every schema file" guidance, so
# there's no harm running this even when a given update didn't touch the
# schema or the frontend.
#
# Usage (on the VM, from the repo root):
#   ./deploy/deploy.sh                 deploy origin/<branch> (fast-forward only)
#   ./deploy/deploy.sh rollback [sha]  back to the previous deploy (or <sha>)
#
# Environment:
#   DEPLOY_BRANCH     branch to deploy (default: the checked-out branch; when
#                     detached after a rollback, the one in data/deploy-state/branch;
#                     else main)
#   DEPLOY_REF        exact commit-ish to deploy (default: origin/$DEPLOY_BRANCH)
#   DEPLOY_BACKUP=1   take a pre-deploy backup even when no schema file changed
#   DEPLOY_NO_TAG=1   do not create the local deploy tag
#   DEPLOY_HEALTH_URL health probe (default: http://127.0.0.1:8000/api/health)
#
# What a run does, in order: fetch; refuse to run on modified tracked files;
# fast-forward to DEPLOY_REF (never a merge commit, never a plain `git pull`);
# record the previous and the new commit under data/deploy-state/; back up
# the database when a schema file changed; apply all schema files in ONE
# transaction; install dependencies; build the frontend into dist.new and
# swap it in; restart; poll /api/health. There is no automatic rollback: on a
# failed deploy the script prints the exact `rollback` command.
#
# Local deploy tags: a successful deploy tags the deployed commit
# `deploy-<UTC timestamp>` in THIS clone only. The tags are NOT pushed: the
# deploy key on the server is read-only, and they are a local trail of what
# ran here, not releases.
#
# Rollback: `rollback` checks out the previous commit (detached), installs
# that version's dependencies, swaps frontend/dist.prev back in (or rebuilds
# when it does not belong to that commit), restarts and runs the health
# check. It does NOT undo database changes: schema files are additive, so an
# older version runs against the newer schema. A restore from a backup is a
# separate, manual step (docs/RUNBOOK.md).
#
# Files managed by ACL on a server (config.yaml, .env, data/): this script
# never chmod/chown/sed -i's them. `sed -i` replaces the file and drops its
# ACL and owner.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE_DIR="data/deploy-state"
HEALTH_URL="${DEPLOY_HEALTH_URL:-http://127.0.0.1:8000/api/health}"

log() { echo "==> $*"; }
die() { echo "ERROR: $*" >&2; exit 1; }

# Refuse to deploy over local edits to tracked files (untracked files such as
# config.yaml, .env and data/ are not tracked and do not count).
deploy_require_clean_tree() {
    if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
        git status --short --untracked-files=no >&2
        die "tracked files have local changes; commit, stash or discard them first."
    fi
}

# Writes one state file without ever chmod-ing an existing one: the
# directory is created owner-only (umask 077), an existing file keeps its
# mode and ACL because `>` truncates in place.
deploy_state_write() {
    local name="$1" value="$2"
    ( umask 077; mkdir -p "$STATE_DIR"; printf '%s\n' "$value" > "$STATE_DIR/$name" )
}

deploy_state_read() {
    local file="$STATE_DIR/$1"
    if [ -f "$file" ]; then head -n1 "$file"; fi
}

# Fetch, then move the checkout to DEPLOY_REF by fast-forward only.
# Sets DEPLOY_PREV_SHA and DEPLOY_NEW_SHA. After a rollback the checkout is
# detached; checking the branch out again first lets the next deploy go
# forward from there.
deploy_update_code() {
    local branch="${DEPLOY_BRANCH:-}"
    if [ -z "$branch" ]; then
        branch="$(git symbolic-ref --short -q HEAD || true)"
    fi
    if [ -z "$branch" ]; then
        branch="$(deploy_state_read branch)"
    fi
    branch="${branch:-main}"
    local ref="${DEPLOY_REF:-origin/$branch}"
    echo "    Deploying branch '$branch' to ref '$ref'"

    git fetch --tags origin
    deploy_require_clean_tree

    DEPLOY_PREV_SHA="$(git rev-parse HEAD)"
    local target
    target="$(git rev-parse --verify "$ref^{commit}")" || die "cannot resolve DEPLOY_REF '$ref'."

    git checkout "$branch"
    git merge --ff-only "$ref" \
        || die "cannot fast-forward '$branch' to '$ref' (diverged local history?). Nothing was changed on disk beyond the checkout."

    DEPLOY_NEW_SHA="$(git rev-parse HEAD)"
    if [ "$DEPLOY_NEW_SHA" != "$target" ]; then
        # DEPLOY_REF names a commit that is behind the branch: a fast-forward
        # cannot move backwards, and silently deploying a newer commit than
        # the one asked for would defeat a fixed target.
        die "checkout is at $DEPLOY_NEW_SHA but DEPLOY_REF resolves to $target; use './deploy/deploy.sh rollback $target' to go back."
    fi

    # Keep the earlier "previous" when nothing changed, so re-running a
    # deploy cannot overwrite the way back with the same commit.
    if [ "$DEPLOY_PREV_SHA" != "$DEPLOY_NEW_SHA" ] || [ -z "$(deploy_state_read previous_sha)" ]; then
        deploy_state_write previous_sha "$DEPLOY_PREV_SHA"
    fi
    deploy_state_write current_sha "$DEPLOY_NEW_SHA"
    deploy_state_write branch "$branch"
    export DEPLOY_PREV_SHA DEPLOY_NEW_SHA
}

# True (exit 0) when a docs/*.sql file differs between two commits.
deploy_schema_changed() {
    local from="$1" to="$2"
    [ "$from" = "$to" ] && return 1
    [ -n "$(git diff --name-only "$from" "$to" -- 'docs/*.sql')" ]
}

# True when the dist.prev directory holds the build of commit $1.
deploy_dist_prev_matches() {
    [ -d frontend/dist.prev ] && [ "$(deploy_state_read dist_prev_sha)" = "$1" ]
}

deploy_build_frontend() {
    ( cd "$APP_DIR/frontend" && npm ci && rm -rf dist.new && npm run build -- --outDir dist.new --emptyOutDir )
}

# dist -> dist.prev, dist.new -> dist: two renames, a failed build never
# reaches this point and leaves dist untouched.
deploy_swap_frontend() {
    ( cd "$APP_DIR/frontend"
      rm -rf dist.prev
      if [ -d dist ]; then mv dist dist.prev; fi
      mv dist.new dist )
    deploy_state_write dist_prev_sha "${DEPLOY_PREV_SHA:-}"
}

deploy_health_check() {
    log "Waiting for $HEALTH_URL ..."
    local body
    if body="$(curl -fsS --retry 15 --retry-delay 2 --retry-connrefused "$HEALTH_URL")" \
        && echo "$body" | grep -q '"status":"ok"'; then
        echo "    health: ok"
    else
        echo "    health check failed (last response: ${body:-none})" >&2
        return 1
    fi
    # A crash loop can answer one probe between restarts.
    sleep 2
    sudo systemctl is-active --quiet eve-trader && echo "    eve-trader: active" \
        || return 1
}

deploy_fail_hint() {
    echo "    eve-trader did not come up healthy." >&2
    sudo journalctl -u eve-trader -n 40 --no-pager >&2 || true
    local prev="${DEPLOY_PREV_SHA:-$(deploy_state_read previous_sha)}"
    echo "    To go back to the previous commit: ./deploy/deploy.sh rollback ${prev}" >&2
    echo "    (rollback does not undo database changes)." >&2
}

deploy_install_backend() {
    log "Installing backend dependencies..."
    .venv/bin/pip install -r requirements.lock -q
    .venv/bin/pip install -e . --no-deps -q
}

deploy_restart_and_verify() {
    log "Restarting eve-trader..."
    sudo systemctl restart eve-trader
    log "Verifying..."
    deploy_health_check || { deploy_fail_hint; exit 1; }
}

# Git part of a rollback (testable without systemd): resolve the target
# (argument, else the recorded previous commit), refuse on a dirty tree, check
# it out detached and record the move. Sets ROLLBACK_FROM and ROLLBACK_TO.
deploy_rollback_checkout() {
    local target="${1:-}"
    if [ -z "$target" ]; then
        target="$(deploy_state_read previous_sha)"
        [ -n "$target" ] || die "no previous deploy recorded in $STATE_DIR; pass a commit: rollback <sha>."
    fi
    target="$(git rev-parse --verify "$target^{commit}")" || die "unknown commit '$target'."

    deploy_require_clean_tree
    ROLLBACK_FROM="$(git rev-parse HEAD)"
    ROLLBACK_TO="$target"
    log "Rolling back $ROLLBACK_FROM -> $ROLLBACK_TO (detached checkout)"
    git checkout --detach "$target"
    deploy_state_write previous_sha "$ROLLBACK_FROM"
    deploy_state_write current_sha "$ROLLBACK_TO"
}

# `rollback [sha]`: detached checkout of the previous (or given) commit.
# No re-exec here on purpose: the target commit may predate this script and
# its own deploy.sh would not know `rollback`; the running copy finishes the
# job (bash has read this whole file's functions already).
deploy_rollback() {
    deploy_rollback_checkout "${1:-}"
    local target="$ROLLBACK_TO" from="$ROLLBACK_FROM"
    DEPLOY_PREV_SHA="$from"
    chmod 644 docs/*.sql

    deploy_install_backend

    log "Restoring frontend..."
    if deploy_dist_prev_matches "$target"; then
        ( cd "$APP_DIR/frontend"
          rm -rf dist.new
          if [ -d dist ]; then mv dist dist.new; fi
          mv dist.prev dist
          rm -rf dist.new )
    else
        echo "    no matching frontend/dist.prev; rebuilding."
        deploy_build_frontend
        ( cd "$APP_DIR/frontend"
          rm -rf dist.prev
          if [ -d dist ]; then mv dist dist.prev; fi
          mv dist.new dist
          rm -rf dist.prev )
    fi
    rm -f "$STATE_DIR/dist_prev_sha"

    deploy_restart_and_verify
    echo "    Rolled back to: $(git log -1 --format='%H (%ci)')"
    echo "    Database changes were NOT undone (schema files are additive)."
    echo "    To return to the newer commit: ./deploy/deploy.sh rollback $from"
    log "Done."
}

deploy_main() {
    cd "$APP_DIR"

    if [ "${1:-}" = "rollback" ]; then
        deploy_rollback "${2:-}"
        return
    fi
    [ $# -eq 0 ] || die "unknown argument '$1' (usage: deploy.sh [rollback [sha]])"

    # The code update (fetch + fast-forward) and the state record happen
    # BEFORE the re-exec below, exactly where the old `git pull` was: the
    # re-executed copy then reads the already-updated script. The commits
    # travel through the environment because the second run sees HEAD ==
    # new and could no longer tell what the previous one was.
    if [ -z "${EVE_TRADER_DEPLOY_REEXECED:-}" ]; then
        log "Updating code (fetch, fast-forward only)..."
        deploy_update_code

        # Confirmed real bug (2026-09-26): a freshly-changed schema file lands on
        # disk with whatever mode the checkout used, which inherits the
        # *invoking interactive shell's* umask - not this script's own. When that
        # shell has the `umask 077` hardening fix from T1-05 active (see backup.py's
        # own comment on the same class of issue, the opposite direction), a
        # schema file touched by the update comes out 0600 (owner-only), unreadable
        # by the `postgres` OS user the schema step below runs as - `psql -f` then
        # fails with a bare "Permission denied" and aborts the whole deploy under
        # `set -e`. Files untouched by a given update keep whatever mode they
        # already had, so this silently only ever hits the specific file(s) a
        # commit just changed - easy to miss until the exact deploy that changes a
        # schema file. Same fix as backup.py's own: chmod explicitly, never
        # depend on the caller's umask. Schema files ONLY - do not widen this
        # to other files.
        chmod 644 docs/*.sql

        # Self-modifying-script guard (confirmed real bug 2026-09-16, found live: a
        # freshly-added schema file - job_category_cost_index_overrides_schema.sql -
        # was silently skipped on the very run that pulled the commit adding it to
        # the loop below). bash opens this script's file descriptor once at process
        # start and keeps reading from it for the rest of the run - the update
        # above can rewrite this file's own later lines (like the schema-apply
        # loop) on disk, but the already-running interpreter doesn't re-open the
        # file to pick that up; it only sees the new content on a *separate*,
        # later invocation. Re-exec once against the now-current file so the rest
        # of this run is guaranteed to read whatever the update just produced, not
        # whatever was on disk when this run started. Uses BASH_SOURCE[0], not $0 -
        # same reasoning APP_DIR above already relies on: $0 can be a path
        # relative to the caller's original cwd (e.g. invoked as
        # `cd deploy && ./deploy.sh`), which the `cd "$APP_DIR"` above would then
        # resolve against the *wrong* directory on re-exec; BASH_SOURCE[0] plus
        # APP_DIR (already absolute) stays correct regardless of how this script
        # was invoked. The env var guards against re-execing forever.
        export EVE_TRADER_DEPLOY_REEXECED=1
        exec "$APP_DIR/deploy/deploy.sh" "$@"
    fi

    local prev_sha="${DEPLOY_PREV_SHA:-$(deploy_state_read previous_sha)}"
    local new_sha
    new_sha="$(git rev-parse HEAD)"
    DEPLOY_PREV_SHA="$prev_sha"

    if [ "${DEPLOY_BACKUP:-}" = "1" ] || deploy_schema_changed "${prev_sha:-$new_sha}" "$new_sha"; then
        log "Pre-deploy database backup (schema files changed or DEPLOY_BACKUP=1)..."
        # Same function the Admin tool and the scheduler use; writes a 0600
        # zip under data/backups. A failure stops the deploy BEFORE the first
        # migration.
        if ! .venv/bin/eve-trader backup create --pre-deploy; then
            die "pre-deploy backup failed - nothing was migrated. Fix the backup (see the log) or deploy without schema changes."
        fi
    else
        log "No schema file changed; skipping the pre-deploy backup."
    fi

    log "Applying Postgres schema (all files, one transaction, idempotent)..."
    # Confirmed real bug (2026-08-30): this list was missing station_trading_
    # schema.sql - each schema file's own tenant_settings_scope_check widening
    # (DROP CONSTRAINT then ADD CONSTRAINT with that file's own, then-current
    # scope list) is only self-consistent in the original chronological order
    # these files were written in. Without station_trading_schema.sql in this
    # loop, doctrine_schema.sql's and refining_schema.sql's own (older,
    # narrower) ADD CONSTRAINT statements fail against a live 'station_trading'
    # row - and since their own DROP CONSTRAINT already succeeded first, this
    # SILENTLY LEFT THE CONSTRAINT MISSING ENTIRELY rather than merely stale
    # (psql's default no-ON_ERROR_STOP behavior doesn't abort the file or this
    # loop on that error either, so this was easy to miss - see ON_ERROR_STOP
    # below, added for the same reason).
    #
    # Adding station_trading_schema.sql to the loop and ON_ERROR_STOP together
    # only turned that silent failure into a loud one, though - they didn't fix
    # the actual root cause: confirmed real again live (2026-08-31),
    # doctrine_schema.sql's and refining_schema.sql's own scope lists had
    # independently drifted stale (missing 'station_trading'), so
    # ON_ERROR_STOP correctly aborted the whole deploy right there, before the
    # loop ever reached station_trading_schema.sql's wider ADD CONSTRAINT that
    # would've put things right again. The actual fix is keeping every schema
    # file's copy of this ALTER's scope list equal to the full, current set
    # (see docs/doctrine_schema.sql's own comment on this same ALTER) - this
    # loop's chronological ordering only matters for tables/columns being
    # introduced, never for this particular shared CHECK constraint.
    #
    # All files run in ONE transaction (`psql -1`): a failure anywhere rolls
    # the whole set back, so a half-applied migration (the failure mode the
    # comments above describe) cannot happen. Probed against an empty and a
    # populated database, twice: no file uses CONCURRENTLY, ADD VALUE or its
    # own BEGIN/COMMIT. A new file that needs one of those must be run
    # separately and this note updated.
    #
    # tests/pg_helpers.py reads this loop to build the test database: keep
    # it a plain `for f in <files>; do` list that starts with phase1.
    schema_args=()
    for f in phase1_schema.sql phase2_schema.sql phase3_schema.sql admin_schema.sql \
             doctrine_schema.sql observability_schema.sql refining_schema.sql \
             station_trading_schema.sql special_orders_schema.sql \
             sorting_schema.sql production_buy_list_schema.sql pipeline_runs_schema.sql \
             session_revocations_schema.sql job_category_cost_index_overrides_schema.sql \
             esi_access_schema.sql portfolio_schema.sql module_reprocessing_schema.sql \
             character_management_schema.sql pi_schema.sql production_ledger_schema.sql \
             asset_manager_schema.sql location_registry_schema.sql; do
        schema_args+=(-f "docs/$f")
    done
    sudo -u postgres psql -d eve_trader -1 -v ON_ERROR_STOP=1 "${schema_args[@]}"

    # If this fails the script stops (set -e) before frontend/dist is swapped.
    deploy_install_backend

    log "Checking legacy access_gate_enabled: false (P5-05)..."
    # Runtime load_access_config forces the gate on unless
    # EVE_TRADER_ALLOW_GATE_OFF is set, so the setting in config.yaml is
    # harmless. This only warns: config.yaml is ACL-managed on a server and
    # an in-place stream edit would replace the file and lose its ACL and
    # owner. Change the line by hand if you want the file to match.
    ALLOW_GATE_OFF="${EVE_TRADER_ALLOW_GATE_OFF:-}"
    if [ -f .env ]; then
      _env_allow=$(grep -E '^[[:space:]]*EVE_TRADER_ALLOW_GATE_OFF=' .env | tail -n1 | cut -d= -f2- | tr -d '[:space:]"' | tr '[:upper:]' '[:lower:]' || true)
      if [ -n "$_env_allow" ]; then
        ALLOW_GATE_OFF="$_env_allow"
      fi
    fi
    case "$(echo "${ALLOW_GATE_OFF:-}" | tr '[:upper:]' '[:lower:]')" in
      1|true|yes) echo "    EVE_TRADER_ALLOW_GATE_OFF set - gate setting in config.yaml is honoured." ;;
      *)
        if [ -f config.yaml ] && grep -qE '^[[:space:]]*access_gate_enabled:[[:space:]]*false\b' config.yaml; then
          echo "    WARNING: config.yaml has access_gate_enabled: false. The gate is forced on at runtime;"
          echo "    change the line by hand so the file matches (this script does not edit config.yaml)."
        fi
        ;;
    esac

    log "Building frontend (into frontend/dist.new)..."
    deploy_build_frontend
    deploy_swap_frontend

    deploy_restart_and_verify

    if [ "${DEPLOY_NO_TAG:-}" != "1" ]; then
        local tag
        tag="deploy-$(date -u +%Y%m%dT%H%M%SZ)"
        git tag "$tag" HEAD
        echo "    Tagged $tag (local only, not pushed)."
    fi
    echo "    Previous commit: ${DEPLOY_PREV_SHA:-unknown}"
    git log -1 --format='    Deployed commit: %H (%ci)'
    echo "    Roll back with: ./deploy/deploy.sh rollback"

    log "Done."
}

# Tests source this file to exercise the functions without running a deploy.
if [ -z "${DEPLOY_SOURCE_ONLY:-}" ]; then
    deploy_main "$@"
    exit $?
fi
