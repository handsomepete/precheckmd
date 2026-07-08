# HOME-447 — Diagnosis & Dry Run

## Dry run output (synthetic fixtures, local file only)

Ran `node mcp/scripts/dry_run_brief.js` — a standalone harness that exercises the
four fixed modules (`mcp/lib/reconciliation.js`, `linearFocus.js`,
`propertyStatus.js`) against fixtures that reproduce today's three
reproducible failures (Hydro One stale amount, HA down, dangling Linear
pin). **No email was sent, no Gmail draft was created, no real YNAB/Linear/HA
API was called.** Output was written to a local scratch file only.

```
# Morning Brief — 2026-07-08 (DRY RUN, synthetic data)

## FINANCE
- Hydro One: paid $454.00 (cleared 2026-07-07) — scheduled amount was stale at $426.00
- Netflix: $20.00 due in 7 day(s) (2026-07-15)

## PROPERTY
- Property status unavailable (HA unreachable)

## TODAY'S LINEAR FOCUS
- Renew HA long-lived token [HOME-451] (fallback — pinned issue "HOME-999" no longer exists, needs cleanup)
```

Compare to today's actual (buggy) output:

```
FINANCE: Hydro One bill ($426.00) due in 2 days          <- stale, already paid at $454
PROPERTY: [raw socket errors per entity, weather + garage] <- should degrade to one line
TODAY'S LINEAR FOCUS: Error: Entity not found: Issue      <- should fall back + log the dangling id
```

See "Scope note" below for why this is a synthetic-fixture dry run rather than
a run of the real production pipeline.

---

## Scope note: this repo does not contain the brief-composition pipeline

Before diagnosing HA, it's worth being explicit about what actually lives
where, since it changes what "fixing the brief" can mean from inside this
repo:

- `mcp/server.js` (this repo) is the **live "PersonalMCP2" MCP tool server** —
  confirmed by its tool list (`get_finances`, `get_scheduled_transactions`,
  `get_home_state`, `get_linear_urgent`, `create_gmail_draft`, `run_brief`,
  etc.) matching the `mcp__PersonalMCP2__*` tools available in this session.
  It runs on the Hetzner box via `pm2 restart homeos-mcp`.
- Its `run_brief` tool does **not** compose the brief itself — it just shells
  out: `spawn('/var/www/cowork/venv/bin/python', ['-m', 'cowork.main', flag])`.
  The actual brief text ("Hydro One bill ($426.00) due in 2 days", the
  PROPERTY section, "TODAY'S LINEAR FOCUS") is produced by that separate
  `cowork` Python package on the box.
- `cowork` is **not present in this git repository or its history**, and no
  repository named `cowork` (or similar) is reachable from this session
  (checked via the repo list). It appears to live only on the Hetzner
  filesystem at `/var/www/cowork`.

Given that, the four fixes below are implemented at the one control point
this repo actually owns: the MCP tool server that `cowork.main` calls into
for finance, Linear, and HA data, plus its `create_gmail_draft` tool (the one
call every brief run makes). Each fix moves the relevant business logic
(reconciliation, focus validation, HA gating) into the tool layer so it's
enforced regardless of what the brief-composition prompt on the other side
does with the data — but if `cowork.main`'s source becomes available, some of
this (especially Task 4's file dual-write) should ideally be driven from
there directly instead of the subject-line heuristic used here. See
inline TODOs in `mcp/lib/briefStore.js`.

The `kb/` directory in this repo (a "second-brain context store" candidate)
exists but is currently empty/unwired — no existing writer, no schema — so
Task 4 targets the dated-file approach instead.

---

## Task 2: Home Assistant TLS diagnosis

**This sandboxed dev environment has no HA credentials and no network path to
the user's home Home Assistant instance.** `HA_URL`, `HA_REFRESH_TOKEN`, and
`HA_CLIENT_ID` live only in a `.env` file on the Hetzner box (correctly never
committed to git — confirmed `.env` is gitignored and no `.env` exists in
this checkout). This session cannot reach the user's home network at all, so
I could not run a live reachability/cert/DNS check against the real
endpoint. That diagnostic has to run **on the Hetzner box itself**, where
`mcp/server.js` actually executes and where the failure was observed.

Run these directly on the box (replace `$HA_URL` with the value from
`/root/mcp/.env` or wherever the live server's env file is):

```bash
# 1. Is the endpoint reachable at all, and what does the TLS handshake look like?
curl -v --max-time 10 "$HA_URL/api/config" -H "Authorization: Bearer <token>"

# 2. Cert expiry / chain, talking to the TLS port directly (strip https:// and any path first)
HOST=$(echo "$HA_URL" | sed -E 's#https?://##; s#/.*##; s#:.*##')
PORT=443   # adjust if HA/tunnel terminates TLS on a non-standard port
echo | openssl s_client -connect "$HOST:$PORT" -servername "$HOST" 2>/dev/null | openssl x509 -noout -dates -subject -issuer

# 3. Does the tunnel/DNS entry resolve, and to where?
dig +short "$HOST"
nslookup "$HOST"

# 4. If this goes through a tunnel (cloudflared/ngrok/Tailscale Funnel/nginx reverse proxy),
#    check that process/service is actually alive:
systemctl status cloudflared 2>/dev/null || pm2 status || docker ps | grep -i tunnel
```

**What the error itself tells us, independent of root cause:** "Client
network socket disconnected before secure TLS connection was established" is
a Node/axios-level error meaning the TCP connection was closed *before* the
TLS handshake completed (not a cert-validation failure, which would be a
different error like `UNABLE_TO_VERIFY_LEAF_SIGNATURE` or
`CERT_HAS_EXPIRED`). Typical causes worth ruling out with the commands
above, roughly in order of likelihood for a tunnel-fronted home HA instance:

1. The tunnel process (cloudflared/ngrok/Tailscale) crashed, restarted, or
   its credential expired — connections hit a dead listener and get reset.
2. The reverse proxy in front of HA expects SNI or a specific `Host` header
   and drops connections that don't match (less likely here since axios
   sends SNI by default, but worth confirming with `openssl s_client
   -servername`).
3. A firewall/NAT rule or the tunnel's idle-connection reaper is closing
   the socket mid-handshake.
4. DNS is resolving to a stale/wrong IP (e.g. dynamic IP changed, tunnel
   re-issued a new hostname).

**Not attempted, per instructions:** no changes to HA infrastructure, the
tunnel, DNS, or certs. This section is diagnosis-only.

### A real, verified code bug found along the way

Independent of the TLS root cause, `get_health` reporting "home_assistant
never connected" is *also* a genuine bug in this repo, now fixed: `mcp/server.js`
defined `markFailed(source, err)` but **never called it anywhere in the file**.
Every tool's error path fell through to a generic `catch` that returned the
error to the caller but never recorded it against `sourceStatus`. So a
source that failed on every single call (exactly the HA case) stayed frozen
at its initial `{ ok: false, last_updated: null, error: null }` forever —
indistinguishable from "never even attempted" in `get_health`'s output.

Verified before/after with a live server instance (no real credentials, so
the YNAB call below fails with an auth error, which is the point — it's
testing that the failure gets *recorded*, not that YNAB succeeds):

```json
// GET /health after a failed tool call, before this fix: (error field never appears, ever)
{"ynab":{"ok":false,"last_updated":null,"stale":true}}

// GET /health after a failed tool call, after this fix:
{"ynab":{"ok":false,"last_updated":null,"stale":true,"error":"Request failed with status code 403"}}
```

Fixed by adding a `TOOL_SOURCE` map (tool name → `sourceStatus` key) and
calling `markFailed(source, err.message)` in the MCP call-tool handler's
catch block. See commit "HOME-447: wire reconciliation/focus/property-status
into the MCP server."

---

## Summary of code changes (see git log for full detail per commit)

| Task | Module | Tool exposed |
|---|---|---|
| 1. Finance reconciliation | `mcp/lib/reconciliation.js` | `get_upcoming_bills` |
| 2. HA consolidation | `mcp/lib/propertyStatus.js` | `get_property_status` |
| 3. Linear focus validation | `mcp/lib/linearFocus.js` | `get_linear_focus` |
| 4. Brief dual-write | `mcp/lib/briefStore.js` | `create_gmail_draft` (extended) + `GET /briefs`, `GET /briefs/:filename` |

All four are pure/injectable modules under `mcp/lib/`, unit tested under
`mcp/test/` with Node's built-in test runner (no test framework dependency).
`mcp/server.js` wires them into the existing tool/handler pattern with no
changes to existing tool behavior or signatures.

## Outstanding TODOs

- `mcp/lib/briefStore.js`: the "is this draft a brief" detection is a
  subject-line heuristic (`/morning brief/i`, `/evening brief/i`, `/\bbrief\b/i`)
  because `cowork.main` (which actually calls `create_gmail_draft`) isn't
  reachable from this repo to pass an explicit brief-type parameter. If/when
  that source is added to a repo, replace the heuristic with an explicit
  argument from that pipeline.
- HA root cause is undiagnosed pending the live commands above being run on
  the Hetzner box — this document only prepared the diagnostic steps and
  fixed the brief's error handling around it.
- Consider having `cowork.main` call the new `get_upcoming_bills`,
  `get_property_status`, and `get_linear_focus` tools directly (instead of
  the raw `get_scheduled_transactions`/`get_home_state`/`get_linear_urgent`
  it presumably calls today) once its source is accessible, so the
  reconciliation/consolidation/fallback logic is actually exercised in
  production rather than just available.
