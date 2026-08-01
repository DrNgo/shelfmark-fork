# ABS Auth Source — Design

**Date:** 2026-08-01
**Status:** Approved (brainstormed with Michael; all four open decisions settled)

## Goal

Add an `abs` auth source to the Shelfmark fork: existing Audiobookshelf (ABS)
users log into Shelfmark with their ABS credentials. Shelfmark validates the
credentials against the ABS server, auto-provisions a local user on first
login, and keeps its own session. Shelfmark never stores ABS passwords.

## Settled decisions

1. **Role mapping:** every ABS login provisions as Shelfmark `user`
   (`sync_role=True` with role always `"user"`). The builtin admin remains the
   only admin. ABS `root`/`admin` types get no special Shelfmark privileges.
2. **ABS-down fallback:** all builtin-source users can still password-login
   under `abs` mode — the exact mirror of how `oidc` mode admits builtin
   users (`is_user_active_for_auth_mode`).
3. **Eligibility:** ABS types `root`, `admin`, `user` qualify; `guest` is
   rejected; `isActive == false` is rejected even with a valid password.
   Rejections count as failed logins for rate limiting.
4. **Existing builtin non-admin users:** kept active via the fallback path.
   A builtin account whose username matches an ABS username is taken over
   (converted to `auth_source=abs`) on first ABS login — the CWA/OIDC
   precedent — **except builtin admins** (see security guard below).

## Approach

Mirror the CWA pattern (the direct precedent: external validation +
auto-provisioning via `upsert_external_user`), plus an `abs_subject` identity
column so linking keys on the stable ABS user id rather than the username.
Rename-safe: an ABS username change re-links to the same Shelfmark account.

Rejected alternatives:
- **Username-only matching (no schema change):** what CWA does; smaller diff
  but an ABS rename silently creates a second Shelfmark account.
- **Refactor `api_login` into per-source strategies first:** large rewrite of
  auth-critical code for one new source. YAGNI.

## Components

### 1. Auth mode plumbing — `shelfmark/core/auth_modes.py`

- `AUTH_SOURCE_ABS = "abs"`, appended to `AUTH_SOURCES`.
- `determine_auth_mode` gains a branch gated like `oidc`:
  `AUTH_METHOD == "abs"` **and** `local_admin_available` **and** ABS login
  configured (`AUDIOBOOKSHELF_ENABLED` truthy + `AUDIOBOOKSHELF_URL`
  non-empty). `load_active_auth_mode` passes those two keys through
  `security_config`.
- `is_user_active_for_auth_mode`: builtin-source users are active under
  `abs` mode too — `auth_mode in (builtin, oidc, abs)`.

### 2. Credential check — `shelfmark/audiobookshelf/client.py`

New `verify_login(username, password)`:
- `POST {base_url}/login` with JSON body, **without** the Bearer header — a
  fresh request, not the token-carrying `self._session`.
- Success → return the ABS user's `id`, `username`, `type`, `isActive`
  (small dataclass or dict).
- HTTP 401 → return `None` (invalid credentials).
- Transport errors (connect/timeout/5xx) → raise, so the route can tell
  "wrong password" from "ABS down".

### 3. Login route — `shelfmark/main.py` `api_login`, new `abs` branch

Order of checks (inside existing rate-limit/lockout wrapper):

1. **Local builtin path first.** If the username belongs to a builtin-source
   user with a password hash and the password matches, log them in without
   ever forwarding that password to ABS. This is both the ABS-down fallback
   and the guarantee that builtin admin credentials never leave Shelfmark.
2. **Forward to ABS** `verify_login` otherwise.
   - `None` (bad credentials) → `_failed_login_response`.
   - `type == "guest"` or `isActive == false` → `_failed_login_response`.
   - Transport error → 503 "Authentication service unavailable" (builtin
     users already had their chance in step 1).
3. **Provision/link** via `upsert_external_user(auth_source="abs",
   username=<abs username>, role="user", sync_role=True,
   subject_field="abs_subject", subject=<abs user id>,
   collision_strategy="takeover", context="abs_login")`.
4. **Session:** `user_id = db_user["username"]` (may be suffixed),
   `db_user_id`, `is_admin` from the DB role, `session.permanent`
   from `remember_me`, `clear_failed_logins`.

**Security guard on takeover:** before the upsert, if the colliding local
username belongs to a **builtin admin** (role `admin`, source `builtin`),
do not take it over — use the `suffix` collision strategy for that login
instead. Without this, whoever controls a matching ABS username could
convert the only admin account to `auth_source=abs` role `user` and lock
admin access. Non-admin builtin accounts take over normally.

### 4. Identity linking — `shelfmark/core/external_user_linking.py` + `user_db`

- `users` table gains nullable `abs_subject` column (migration mirrors
  however `oidc_subject` was added).
- `UserDB.get_user(abs_subject=...)` lookup.
- `_get_by_subject`, `_build_updates`, and the create path in
  `upsert_external_user` learn `subject_field == "abs_subject"`, line-for-line
  parallel to the `oidc_subject` handling.
- ABS user records carry no email → no email linking (`allow_email_link`
  stays `False`).

### 5. Settings — security tab registration

- AUTH_METHOD select gains option `abs` labeled "Audiobookshelf".
- Save-time validation: selecting `abs` requires the ABS connection
  (`AUDIOBOOKSHELF_ENABLED` + `AUDIOBOOKSHELF_URL`) to be configured;
  otherwise reject the save with a clear message.
- Settings remain env-over-config on boot (`settings_registry.py`
  `_parse_env_value`) — no new env vars required.

### 6. Frontend — small additions

- `AdminAuthSource` union + `authSourceLabel` ("Audiobookshelf") in
  `src/frontend/src/services/api.ts` / `useUserMutations.ts`.
- Badge variant in `UserAuthSourceBadge`.
- AUTH_METHOD select option label comes from the settings registry.
- Login page: no changes — `abs` is not `oidc`, so the existing
  `PasswordLoginForm` renders.

## Error handling summary

| Condition | Response |
|---|---|
| Bad ABS credentials | 401 via `_failed_login_response` (rate-limited) |
| ABS guest / inactive user | 401 via `_failed_login_response` (rate-limited) |
| ABS unreachable, non-builtin user | 503 "Authentication service unavailable" |
| ABS unreachable, builtin user w/ valid password | logged in (step 1, never reaches ABS) |
| `abs` selected but ABS unconfigured | `determine_auth_mode` returns "none"; settings save rejected |

## Security notes

- ABS passwords are forwarded in-cluster (`http://audio-book-shelf.media.svc`)
  and never persisted; Shelfmark keeps its own session.
- Builtin-first check keeps builtin (admin) passwords from ever reaching ABS.
- Builtin-admin takeover guard (above).
- Verification of "auth works" on the public deployment
  (shelfmark.drngos.net) must use a **data endpoint returning 401**
  unauthenticated — SPA-shell 200s prove nothing.
- `/security-review` runs on the branch before merge.

## Testing

- **Unit:** `determine_auth_mode` abs gating (configured/unconfigured/no
  local admin); `is_user_active_for_auth_mode` builtin-under-abs;
  `verify_login` against mocked HTTP (success, 401, timeout, malformed JSON).
- **E2E (mirror `test_login_cwa_provisions_db_user`):** first ABS login
  provisions a `user`-role account with `abs_subject` set; second login
  re-links by subject after ABS username rename; guest rejected; inactive
  rejected; builtin user logs in with ABS down; builtin-admin username
  collision does not take over the admin; unauthenticated data endpoint
  returns 401 in abs mode.
- **Frontend:** `npm run typecheck && npx oxlint src/ && npm run format:check
  && npm run test:unit && npm run build` from `src/frontend`.
- **Conventions:** `except A, B:` is valid PEP 758 (Python 3.14) — do not
  "fix" it.

## Out of scope

- Periodic ABS user sync (CWA-style batch sync). Login-time provisioning only.
- ABS token/OAuth flows; only username/password `POST /login`.
- Role sync beyond forcing `user`.
