# ABS Auth Source — Design

**Date:** 2026-08-01
**Status:** Approved; revised after Codex security review (fail-closed mode
resolution, eligibility allowlist, standalone login verifier, hardened
linking/rate-limit semantics).

## Goal

Add an `abs` auth source to the Shelfmark fork: existing Audiobookshelf (ABS)
users log into Shelfmark with their ABS credentials. Shelfmark validates the
credentials against the ABS server, auto-provisions a local user on first
login, and keeps its own session. Shelfmark never stores ABS passwords.

## Settled decisions

1. **Role mapping:** every ABS login provisions as Shelfmark `user`
   (`sync_role=True` with role always `"user"`). The builtin admin remains the
   only admin. ABS `root`/`admin` types get no special Shelfmark privileges.
2. **ABS-down fallback:** builtin-source users can still password-login under
   `abs` mode — the mirror of how `oidc` mode admits builtin users
   (`is_user_active_for_auth_mode`) — **unless `DISABLE_LOCAL_AUTH` is set**,
   which disables the builtin fallback step exactly as it disables password
   login in builtin/oidc modes. ABS validation itself is not local auth and
   still proceeds under that flag.
3. **Eligibility (allowlist, not blacklist):** the ABS login response must
   carry a nonempty `id`, a nonempty `username`, `type` in
   `{"root", "admin", "user"}` (unknown/missing types are rejected), and
   `isActive is True` (missing or non-boolean rejected). Rejections count as
   failed logins for rate limiting.
4. **Existing builtin non-admin users:** kept active via the fallback path.
   A builtin non-admin account whose username matches an ABS username is
   taken over (converted to `auth_source=abs`, keeping its id and history) on
   first ABS login — the CWA/OIDC precedent. **Accepted risk, single-operator
   trust model:** this deployment treats ABS as the trusted identity
   authority run by the same operator; an ABS admin can thereby acquire a
   same-named local non-admin account's data. Builtin **admins** are never
   taken over (security guard below).
5. **Fail closed, not open:** unlike `oidc`/`proxy`/`cwa` (whose missing
   prerequisites make `determine_auth_mode` return `"none"`, which the
   middleware treats as *no auth required*), `abs` mode must never degrade to
   `"none"` because ABS config disappears. See §1.
6. **Sessions vs. ABS deactivation:** deactivating/deleting an ABS user takes
   effect at next login; existing Shelfmark sessions live until logout or
   expiry (≤7 days with remember-me). No session-revalidation middleware.
   Documented accepted behavior.
7. **Rename semantics:** linking keys on the stable ABS user id, so an ABS
   username rename keeps the same Shelfmark account. The **local Shelfmark
   username intentionally stays unchanged** (no collision-prone auto-rename);
   only the subject link is stable.
8. **Transport:** the ABS URL may be plain HTTP **only for trusted in-cluster
   networks** (this deployment: `http://audio-book-shelf.media.svc`). The
   security-settings save emits a warning when an `http://` URL is configured
   with `abs` auth. HTTPS URLs use the repository's standard TLS verification
   (`get_ssl_verify`).

## Approach

Mirror the CWA pattern (external validation + auto-provisioning via
`upsert_external_user`), plus an `abs_subject` identity column so linking keys
on the stable ABS user id rather than the username.

Rejected alternatives:
- **Username-only matching (no schema change):** what CWA does; smaller diff
  but an ABS rename silently creates a second Shelfmark account.
- **Refactor `api_login` into per-source strategies first:** large rewrite of
  auth-critical code for one new source. YAGNI.

## Components

### 1. Auth mode plumbing — `shelfmark/core/auth_modes.py`

- `AUTH_SOURCE_ABS = "abs"`, appended to `AUTH_SOURCES`.
- `determine_auth_mode` gains: `if auth_mode == AUTH_SOURCE_ABS and
  local_admin_available: return AUTH_SOURCE_ABS`. **Deliberately does NOT
  gate on ABS connection config** — if `AUTH_METHOD=abs` is selected but ABS
  is unconfigured/unreachable, the mode stays `abs`: protected endpoints
  still demand a session, builtin admins can still log in, and ABS login
  attempts get 503. This is the fail-closed resolution of the existing
  "missing prerequisites → `none` → wide open" pattern; ABS-configured
  enforcement lives in settings save validation (§5) and the runtime 503.
- `is_user_active_for_auth_mode`: builtin-source users are active under
  `abs` mode too — `auth_mode in (builtin, oidc, abs)`.
- `normalize_auth_source` is **not** extended: every ABS-provisioned row
  always carries an explicit `auth_source="abs"` (set by `upsert_external_user`
  on both create and update), so no inference from `abs_subject` is needed.

### 2. Credential check — `shelfmark/audiobookshelf/client.py`

Standalone module-level function (no client instance, no API token — the
existing `AudiobookshelfClient`/`build_client_from_config` contract stays
bearer-token, read-only GET; its docstring gains a note pointing here):

`verify_abs_login(url, username, password, *, timeout=30) -> AbsLoginUser | None`

- `POST {normalize_http_url(url)}/login` with JSON body
  `{"username": ..., "password": ...}`, fresh `requests.post` — **never**
  the token-carrying session, no Authorization header, no redirect following
  (`allow_redirects=False`).
- Uses `timeout=timeout` and `verify=get_ssl_verify(url)` like `_request`.
- **Success (200, valid JSON):** parse `payload["user"]` and return a small
  dataclass `AbsLoginUser(id, username, type, is_active)` with fields
  normalized to `str`/`bool`. Missing `user` object, missing/empty `id` or
  `username`, or non-dict JSON → treated as invalid (`None`) and logged.
- **401 or 403:** return `None` (invalid credentials).
- **Any other HTTP status, malformed/oversized JSON:** raise `ValueError`
  (route maps to 503) — bounded parse, never a traceback into the response.
- **Transport errors** (connect/timeout): propagate
  `requests.exceptions.RequestException` so the route can distinguish
  "wrong password" from "ABS down".

### 3. Login route — `shelfmark/main.py` `api_login`, new `abs` branch

Rate limiting uses a **canonical key** for all lockout calls in this branch
(`is_account_locked` / `record_failed_login` / `clear_failed_logins`):
`username.strip().lower()` — so case/whitespace variants of one identifier
share a counter.

Order of checks:

1. **Local builtin path first** (skipped entirely when `DISABLE_LOCAL_AUTH`):
   load `user_db.get_user(username=...)`; if the row's
   `normalize_auth_source(auth_source, oidc_subject) == "builtin"` **and** it
   has a `password_hash` **and** `check_password_hash` passes → log in.
   The builtin password never leaves Shelfmark. A taken-over `abs`-source row
   fails the source check, so a stale local hash on it is inert.
2. **Resolve ABS URL** from config (`AUDIOBOOKSHELF_ENABLED` +
   `AUDIOBOOKSHELF_URL`); missing/disabled → 503 "Authentication service
   unavailable" (fail closed, no session).
3. **Forward to ABS** `verify_abs_login` inside
   `try/except requests.exceptions.RequestException, ValueError:` → 503.
   - `None` (bad credentials) → `_failed_login_response`.
   - Eligibility allowlist failure (decision 3) → `_failed_login_response`.
4. **Provision/link** via `upsert_external_user(auth_source="abs",
   username=<abs username>, role="user", sync_role=True,
   subject_field="abs_subject", subject=<abs user id>,
   collision_strategy="takeover", context="abs_login")`, wrapped in
   try/except (`_OPERATIONAL_ERRORS` + `ValueError`): any exception or a
   `None`/`"not_found"` result → generic 500 "Authentication system error",
   **no session created**, no DB details disclosed.
5. **Session:** `user_id = db_user["username"]` (may be suffixed),
   `db_user_id`, `is_admin` from the DB role, `session.permanent` from
   `remember_me`, `clear_failed_logins` (canonical key).

**Security guard on takeover:** before the upsert, if the local username
collision target is a **builtin admin** (role `admin`, normalized source
`builtin`), pass `collision_strategy="suffix"` for that call instead.
Without this, whoever controls a matching ABS username could convert the only
admin account to `auth_source=abs` role `user` and lock admin access.

### 4. Identity linking — `shelfmark/core/external_user_linking.py` + `shelfmark/core/user_db.py`

`abs_subject` is an **explicitly allowlisted** subject field, mirrored
line-for-line on `oidc_subject` — never a dynamic column name in SQL. All
four `UserDB` surfaces change:

1. **Schema + migration:** `abs_subject TEXT UNIQUE` (nullable), added the
   same way the `oidc_subject` column/migration was added.
2. **Create:** `create_user(..., abs_subject=None)` and its INSERT.
3. **Lookup:** `get_user(abs_subject=...)`.
4. **Update:** `abs_subject` joins the update allowlist/statements.

In `external_user_linking.py`: `_get_by_subject`, `_build_updates`, and the
create path each add an explicit `subject_field == "abs_subject"` branch next
to the existing `"oidc_subject"` one.

ABS user records carry no email → no email linking (`allow_email_link` stays
`False`).

### 5. Settings — security tab registration

- AUTH_METHOD select gains option `abs` labeled "Audiobookshelf".
- Save-time validation: selecting `abs` requires `AUDIOBOOKSHELF_ENABLED`
  and a nonempty `AUDIOBOOKSHELF_URL`; otherwise reject the save with a clear
  message. (`AUDIOBOOKSHELF_API_TOKEN` is **not** required for auth — the
  verifier is unauthenticated — only for the library-matching features.)
- Save-time warning (non-blocking) when the configured URL is `http://`
  (decision 8).
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
| Bad ABS credentials (401/403 from ABS) | 401 via `_failed_login_response` (rate-limited) |
| ABS user fails eligibility allowlist | 401 via `_failed_login_response` (rate-limited) |
| ABS unreachable / unexpected status / malformed response | 503 "Authentication service unavailable" |
| ABS unconfigured while `AUTH_METHOD=abs` | mode stays `abs` (fail closed); ABS logins 503; builtin fallback works |
| Provisioning failure (`upsert_external_user` raises or returns None) | 500 "Authentication system error", no session |
| ABS unreachable, builtin user w/ valid password | logged in (step 1, never reaches ABS) |
| `DISABLE_LOCAL_AUTH=true` | builtin fallback skipped; ABS validation unaffected |
| `abs` selected in settings without ABS configured | save rejected |

## Security notes

- ABS passwords are forwarded in-cluster and never persisted; Shelfmark keeps
  its own session. HTTP transport is an explicit trusted-in-cluster-only
  allowance (decision 8) with a save-time warning.
- Builtin-first check keeps builtin (admin) passwords from ever reaching ABS.
- Builtin-admin takeover guard; non-admin takeover is a documented accepted
  risk under the single-operator trust model (decision 4).
- Fail-closed mode resolution (decision 5) — `abs` never degrades to the
  no-auth `"none"` mode.
- Eligibility is allowlist-based; malformed ABS payloads are rejected.
- Rate limiting keys on the casefolded, trimmed username in the abs branch.
- Verification of "auth works" on the public deployment
  (shelfmark.drngos.net) must use a **data endpoint returning 401**
  unauthenticated — SPA-shell 200s prove nothing.
- `/security-review` runs on the branch before merge.

## Testing

- **Unit — auth modes:** `determine_auth_mode` returns `abs` with a local
  admin regardless of ABS config (fail-closed); returns `none` without a
  local admin (matching builtin/oidc); `is_user_active_for_auth_mode`
  admits builtin under `abs`.
- **Unit — verifier:** `verify_abs_login` against mocked HTTP: success;
  401/403 → None; timeout/connect error → raises RequestException;
  500 / non-JSON / missing `user` / empty id → ValueError or None per §2;
  no Authorization header on the outbound request; redirects not followed.
- **E2E (mirror `test_login_cwa_provisions_db_user`):** first ABS login
  provisions a `user`-role account with `abs_subject` set and
  `auth_source="abs"`; second login after ABS username rename re-links by
  subject (local username unchanged); guest rejected; unknown type rejected;
  inactive and missing-`isActive` rejected; builtin user logs in with ABS
  down; `DISABLE_LOCAL_AUTH` blocks the builtin fallback but not ABS login;
  builtin-admin username collision suffixes instead of taking over;
  non-admin builtin collision takes over; taken-over row's old password no
  longer authenticates in abs mode; provisioning failure yields 500 and no
  session; `abs_subject` uniqueness enforced; unauthenticated data endpoint
  returns 401 in abs mode, including when ABS is unconfigured.
- **Frontend:** `npm run typecheck && npx oxlint src/ && npm run format:check
  && npm run test:unit && npm run build` from `src/frontend`.
- **Conventions:** `except A, B:` is valid PEP 758 (Python 3.14) — do not
  "fix" it.

## Out of scope

- Periodic ABS user sync (CWA-style batch sync). Login-time provisioning only.
- Session revocation on ABS deactivation (decision 6).
- ABS token/OAuth flows; only username/password `POST /login`.
- Role sync beyond forcing `user`.
- Fixing the pre-existing fail-open behavior of `oidc`/`proxy`/`cwa`
  prerequisite loss (upstream pattern; `abs` simply doesn't adopt it).
