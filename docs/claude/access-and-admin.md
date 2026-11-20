# Tool permissions, access gate and Admin

> Moved out of CLAUDE.md on 2026-11-20 to keep that file small. Section names are
> unchanged, so a code comment citing `CLAUDE.md "<section>"` finds its section
> here (CLAUDE.md lists which file holds which section). "This file", "above"
> and "below" in the text may refer to CLAUDE.md or another file in `docs/claude/`.

## Tool permissions & Admin

Two independent authorization layers, don't conflate them: **tenant_id**
(RLS, "whose data") and **tool grants** (`tool_grants` table, "which tools
can this specific character see/use"). A valid access-gate session cookie
is not enough by itself: `AccessGateMiddleware` (`api/app.py`) re-validates
it on every gated request via `access_gate.authorize_session_cookie` (one
DB read: `storage.session_authorization` joins `tenant_registry_entries` to
`tool_grants` with `e.tenant_id = ?` and `g.tenant_id = e.tenant_id`, and
to `character_session_revocations` so a registry DELETE cannot resurrect
an old cookie). The
character must still belong to the cookie's tenant, the itsdangerous issue
timestamp must not precede `sessions_valid_after`, and the required
`tool_key` must be in that tenant-scoped grant list. Missing registry row
→ 401; registered but missing grant → 403. The session cookie payload also
carries `iat_ms` (issue time in milliseconds): with it a cookie is revoked
when `iat_ms` is before `sessions_valid_after` (floored to ms, the same ms
stays valid); cookies without it keep the old whole-second rule.
`AuthorizedSession.issued_at` exposes the issue time. `/docs`, `/redoc` and
`/openapi.json` need a valid session **and** the `admin` grant while the gate
is on. SSO callback redirects carry only fixed error codes in `?message=`
(`sso_error`, `state_expired_or_unknown`, `nonce_mismatch`, ...); details go
to the log, never the URL. The pending-login `state` is random, an entry is
consumed only when the browser nonce matches, and the per-IP limit groups
IPv6 by /64. A Characters SSO round without a captured tenant fails with
`session_lost`; only the `gate` and `reauth` flows exist. A refresh rejected
by SSO (`invalid_grant`) is stored as `TokenRecord.refresh_rejected_at`; the
token manager then raises `RefreshTokenRejected` without calling SSO again,
the ESI client maps it to the 400 "Token refresh failed" status error (dead
owner, stale clear unchanged), and the Characters list reports
`refresh_rejected`. `/api/gate/status`'s own
`tools` field uses the same `authorize_session_cookie` chokepoint so the
Landing cards and the middleware cannot disagree. Both layers are no-ops
while `AccessConfig.access_gate_enabled` is `False` (trusted local
operator, filesystem/SSH only — not a request parameter).

`access_gate.DEFAULT_TOOL_KEYS` is every tool in `ALL_TOOL_KEYS` except
`admin` and `module_reprocessing`. Admin's Add User grants it, approving an
access request preselects it, and `eve-trader admin grant-defaults` applies
it to existing tenants.

The tool-key table (router prefix, page, default grant, ESI-consuming) is in
CLAUDE.md, "Tool keys".

`tool_grants` (`character_id, tool_key, tenant_id`) is deliberately **not
RLS-scoped**, same reasoning as `tenants`/`tenant_registry_entries`
(`docs/phase3_schema.sql`) - queried via `storage.connect_unscoped()`.
`global_structure_names` (`docs/admin_schema.sql`, Manual tracking plan
phase 2) is the same kind of exception for a different reason: every
tenant's successful structure-name resolution is deliberately shared
across tenants (a structure's real-world name isn't tenant-private data,
decision Q2 in `docs/MANUAL_TRACKING_PLAN.md`), so it has no `tenant_id` at
all - `storage.get/upsert_global_structure_name`, both `connect_unscoped()`.
`access_allowlist` and `access_requests` are unscoped the same way: the
allowlist decides who may *request* access, and a request exists before
any tenant does. The
Admin tool (`eve_trader/admin.py`'s `do_*` functions, `api/routers/
admin.py`, tool_key `"admin"`) is a deliberate **cross-tenant superadmin**
surface, not a per-tenant self-service page. `"admin"` is a normal grant
(no `DEFAULT_TENANT_ID` bypass). First admin on a gated install is created
with `eve-trader admin bootstrap` (see `docs/OPERATOR_SECURITY.md`). No
other character can reach `/api/admin` unless they have that tool_key.

`"characters"` is a normal grant in `ALL_TOOL_KEYS` (no
`DEFAULT_TENANT_ID` bypass). Admin's tool checkboxes auto-tick it when any
ESI-consuming tool is selected — UI only; `admin.do_set_tool_grants` stays
replace-not-merge and does not insert `"characters"` on the server. The
Characters router (`/api/characters/`) is gated on that grant via
`_TOOL_PATH_PREFIXES`. Prefix `/api/auth/{role_prefix}/start` is gone (Phase 9). ESI tokens are
re-authorized from `/api/characters/reauth/start`; a character that holds
no token yet is registered from `/api/characters/add/start` (identity
only, no scopes — an already-registered character is a deliberate no-op
there, since the scope-less write would clobber the key
`reauth_write_role` reuses). Token *storage* still
uses prefix keys (`producer:<id>`, …); `ROLE_PREFIX_TOOL` /
`validate_role_key_for_tool` still own those namespaces for DELETE.

**SSO identity.** The login identity is the access token's JWT, verified locally
(`TokenManager._identity`: RS256 against the JWKS cached class-wide for 1 h,
one reload on an unknown `kid`, issuer, both audiences, 60 s leeway). A key
endpoint outage fails the login as `identity_failed`; token refresh never
verifies a JWT. Stored scopes are the granted `scp`, not the requested ones.
The character owner hash is stored on `tenant_registry_entries.owner_hash`
(first gate login) and on token records; a changed hash is only logged
(`storage.record_owner_hash`), it never blocks a login or overwrites the stored
value (a lock-out waits for confirmation that transfers change the hash).

`AccessGate` is character-only (`tenant_registry_entries.entry_type`
CHECK-constrained to `'character'`, `docs/admin_schema.sql`) - corp/alliance
registry entries were retired once tool-level permissions made "any
character in this corp/alliance gets full access" too coarse. Before
narrowing that constraint on a real deployment, check for existing
corp/alliance rows first (`SELECT entry_type, count(*) FROM
tenant_registry_entries GROUP BY entry_type`) - narrowing without migrating
them first locks those characters out.

The corp/alliance **allowlist** (`access_allowlist`) does not bring those
registry entries back. It only decides who may file an access request.
Approval still creates one new tenant for that character and the tool
grants the admin picked. While the allowlist is empty, nothing is
re-checked and existing users keep access. Characters who hold `admin` are
exempt from the re-check. Everyone else is checked at login and, during a
session, when `affiliation_checked_at` is older than 6 hours
(`access_policy.py`; no scheduler). If ESI is down, the last stored
affiliation is used for up to 7 days; older than that the login is
`?gate=error` and an in-session request is 403 `access_unverifiable`.
Failing the check sets `tenant_registry_entries.access_suspended` and
withholds the cookie (`?gate=suspended`). The tenant and its data stay.
Login always re-checks and clears the flag when the character is allowed
again. `/api/gate/status` does the same when the last check is older than
6 hours, so the existing cookie works again without issuing a new one.
An API request that already sees the flag returns 403 `access_suspended`
and does not clear it. A rejected request stays rejected until an admin
deletes the row. Pending requests whose corp/alliance leaves the allowlist stay open
and are flagged. The pending count is returned only to sessions that hold
`admin` (`pending_access_requests` on `/api/gate/status`).
