# HOME-447 — Deploy Runbook (execute on the Hetzner box)

Copy-paste ready. `<TOKEN>`, `<BOX_HOST>`, `<COWORK_DIR>` etc. are the only
placeholders — fill them in once at the top of your session and reuse.

```bash
export TOKEN='<TOKEN>'                    # the real BEARER_TOKEN value, see step 0
export REPO_DIR='<PATH_TO_precheckmd_ON_BOX>'
export COWORK_DIR='/var/www/cowork'       # confirm this is still correct
export MCP_PORT=3848                      # confirm against the box's .env / pm2 config
```

---

## READ THIS FIRST — deploying this branch WILL likely break the morning brief

This branch makes `POST /mcp` require `Authorization: Bearer <BEARER_TOKEN>`
on every call, and makes the **server refuse to start at all** if
`BEARER_TOKEN` isn't set. Before this branch, `/mcp` had **no auth check
whatsoever** — anyone (or anything) on the box, or anything that could reach
the port, could call any tool with zero credentials.

Two live consumers depend on this endpoint:

1. **claude.ai** (this session's own "PersonalMCP2" connector) — calling
   `POST /mcp` with a bearer token. This is *probably* already fine: setting
   up an authenticated MCP connector in Claude normally means a token was
   already configured on the client side, even though the server never
   actually checked it before now. **Not verified from this repo — confirm
   in step 3 below before assuming it's fine.**
2. **`cowork.main`** — the brief-composition pipeline that `run_brief`
   spawns on this same box. Its source is not in this repository (confirmed
   — see DIAGNOSIS.md/INTEGRATION.md), so **it could not be checked from the
   review that produced this branch.** Given `/mcp` was completely open
   before this change, and `cowork.main` is a same-box, same-process-family
   caller that had zero functional reason to ever send a bearer token, **the
   most likely outcome is that cowork.main is NOT currently sending
   `Authorization: Bearer ...` and every tool call it makes will start
   returning 401 the moment this deploys** — silently breaking the morning
   and evening brief (it'll either fail outright or, worse, run on stale/
   partial data if `cowork.main` doesn't treat 401s as fatal).

**This was a deliberate trade-off, not an oversight**: the auth fix was not
weakened to accommodate this uncertainty, because an unauthenticated tool
endpoint that can call `delete_transaction`, `control_home`, and
`create_gmail_draft` is a worse problem than a broken brief for a day.
**Do the pre-flight checks in step 2 before restarting the service** —
specifically, confirm whether `cowork.main` sends a bearer token today, and
if it doesn't, have step "b" below ready to apply in the *same* maintenance
window, or accept that the brief will break until it's applied.

**Even more urgent than #2: if `BEARER_TOKEN` isn't set in the box's `.env`
today, the server will fail to start at all** — not a partial degradation,
a total outage of every tool, for claude.ai and cowork.main both. Step 1
checks this first, before you restart anything.

---

## 0. One-time: get the real token value

```bash
grep '^BEARER_TOKEN=' "$REPO_DIR/mcp/.env" 2>/dev/null || grep '^BEARER_TOKEN=' /root/mcp/.env 2>/dev/null
# copy the value after '=' into $TOKEN above. If nothing printed, see step 1 below.
```

---

## 1. Pre-flight: is BEARER_TOKEN even set today?

```bash
if grep -q '^BEARER_TOKEN=.\+' "$REPO_DIR/mcp/.env" 2>/dev/null; then
  echo "BEARER_TOKEN is set — safe to proceed to step 2."
else
  echo "!!! BEARER_TOKEN IS NOT SET !!!"
  echo "Restarting after deploying this branch will crash-loop the server (fail-closed by design)."
  echo "Generate one now, e.g.: openssl rand -hex 32"
  echo "Then add 'BEARER_TOKEN=<value>' to $REPO_DIR/mcp/.env before continuing."
fi
```

If you need to generate a new one:
```bash
openssl rand -hex 32
# append: echo "BEARER_TOKEN=<paste_generated_value>" >> "$REPO_DIR/mcp/.env"
```
If you generate a **new** token here (rather than reusing whatever was
already configured), remember: any existing claude.ai connector config also
needs updating to match, or its calls will start 401ing too.

---

## 2. Check whether cowork.main sends a bearer token today

```bash
grep -rn "Authorization\|Bearer\|BEARER_TOKEN\|/mcp\b" "$COWORK_DIR" \
  --include="*.py" --include="*.env*" --include="*.cfg" --include="*.ini" 2>/dev/null
```

- **If you see an `Authorization: Bearer ...` header being built** using the
  same token as `$TOKEN` — you're covered, skip to step 3.
- **If you see nothing**, or it's calling `http://127.0.0.1:$MCP_PORT/mcp`
  (or similar) with no auth header — this is the break described above.
  Find the HTTP client call (likely `requests.post(...)` or `httpx.post(...)`
  to a URL ending in `/mcp`) and add the header, e.g.:

  ```python
  # before:
  resp = requests.post(mcp_url, json=payload)

  # after:
  resp = requests.post(
      mcp_url,
      json=payload,
      headers={"Authorization": f"Bearer {os.environ['BEARER_TOKEN']}"},
  )
  ```

  and make sure `cowork.main`'s own environment (its `.env`, systemd unit,
  or wherever `$COWORK_DIR` sources env vars from) has `BEARER_TOKEN` set to
  the **same value** as `$TOKEN`. Don't skip this — restarting the MCP
  server without also fixing this will break the brief immediately.

Also see INTEGRATION.md §"Prerequisite" for the same requirement in the
context of wiring the new tools.

---

## 3. Deploy

```bash
cd "$REPO_DIR"
git fetch origin main
git log HEAD..origin/main --oneline            # sanity-check what's coming in
git diff HEAD origin/main -- mcp/package-lock.json | head -5   # did deps change?
git checkout main
git pull origin main
```

Install deps only if the lockfile diff above showed changes (it does, this
branch adds `mcp/package.json` + `mcp/package-lock.json` for the first time):
```bash
cd "$REPO_DIR/mcp"
npm ci
```

Confirm/set `BEARER_TOKEN` in whatever the service actually reads (`.env`
next to `server.js`, or the systemd unit / pm2 ecosystem file's `env` block
— check both, they can drift):
```bash
grep '^BEARER_TOKEN=' "$REPO_DIR/mcp/.env"
# or, if run via pm2 ecosystem file:
pm2 env homeos-mcp | grep BEARER_TOKEN
```

Restart:
```bash
pm2 restart homeos-mcp
sleep 1
pm2 logs homeos-mcp --lines 20 --nostream
```

**If the log shows `FATAL: BEARER_TOKEN is not set` and the process is not
`online` in `pm2 status`** — you skipped step 1. Set `BEARER_TOKEN` and
`pm2 restart homeos-mcp` again.

---

## 4. Verify

```bash
# POST /mcp without a token -> expect 401
curl -s -o /dev/null -w "no-token: %{http_code}\n" -X POST "http://127.0.0.1:$MCP_PORT/mcp" \
  -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_health","arguments":{}}}'

# POST /mcp with the token -> expect 200
curl -s -o /dev/null -w "with-token: %{http_code}\n" -X POST "http://127.0.0.1:$MCP_PORT/mcp" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_health","arguments":{}}}'

# GET /briefs -> expect 401 then 200
curl -s -o /dev/null -w "briefs no-token: %{http_code}\n" "http://127.0.0.1:$MCP_PORT/briefs"
curl -s -o /dev/null -w "briefs with-token: %{http_code}\n" -H "Authorization: Bearer $TOKEN" "http://127.0.0.1:$MCP_PORT/briefs"
```

Expected: `no-token: 401`, `with-token: 200`, `briefs no-token: 401`,
`briefs with-token: 200`. Anything else — stop and investigate before
considering this deployed.

**Then trigger a real (or dry) brief and confirm it actually completes** —
this is the only way to know step 2 was handled correctly, since a 401 loop
in `cowork.main` may not be obviously visible from the MCP server's side
alone. Check `pm2 logs` for both `homeos-mcp` and whatever process name
`cowork.main` runs under during/after the next scheduled brief.

---

## 5. Log audit — who's been hitting POST /mcp

**Known gap, check this first:** `mcp/server.js` has no request-logging
middleware (no morgan/winston, nothing logging per-request access) — so
there is likely **no historical record inside the app itself**. Whatever you
can audit depends on what sits in front of it:

```bash
# Is there an nginx reverse proxy?
ls -la /etc/nginx/sites-enabled/ 2>/dev/null
ls -la /var/log/nginx/ 2>/dev/null

# Is there a Cloudflare Tunnel (cloudflared)? Local logs are usually just
# connection/error logs, not per-request access — check the Cloudflare
# Zero Trust dashboard's tunnel analytics for real request-level history.
systemctl status cloudflared 2>/dev/null
sudo journalctl -u cloudflared --since "14 days ago" | grep -i error

# What does pm2 actually have buffered/rotated for the app itself?
pm2 logs homeos-mcp --lines 5000 --nostream | grep -i "POST /mcp\|error"
ls -la ~/.pm2/logs/ 2>/dev/null
```

**If nginx access logs exist**, extract and summarize `POST /mcp` requests:
```bash
# Plain logs:
grep '"POST /mcp' /var/log/nginx/access.log | awk '{print $1}' | sort | uniq -c | sort -rn

# Include rotated/gzipped logs too:
zgrep -h '"POST /mcp' /var/log/nginx/access.log*.gz /var/log/nginx/access.log 2>/dev/null \
  | awk '{print $1}' | sort | uniq -c | sort -rn

# Date range covered:
grep '"POST /mcp' /var/log/nginx/access.log | awk -F'[' '{print $2}' | awk '{print $1}' | sort -u | head -1
grep '"POST /mcp' /var/log/nginx/access.log | awk -F'[' '{print $2}' | awk '{print $1}' | sort -u | tail -1

# Per-IP, per-day breakdown:
grep '"POST /mcp' /var/log/nginx/access.log \
  | awk -F'[' '{print $1, $2}' \
  | awk '{print $1, substr($3,1,11)}' \
  | sort | uniq -c | sort -rn
```

**Filter out your own traffic before drawing conclusions:**
```bash
# Your own current egress IP (run this from a machine/network you trust):
curl -s ifconfig.me
```
There is no single published, stable IP range for "Anthropic egress" I can
give you here with confidence — Claude/claude.ai connector traffic to a
self-hosted MCP server like this one typically originates from wherever
*your own* client session runs (e.g. your machine, or this box itself for
localhost calls), not from a fixed Anthropic-owned CIDR block, since MCP
connectors work by the client (Claude) calling out to your server, not the
reverse. Cross-check the timestamps of "unknown" IPs against your own usage
first (when you were actually using claude.ai) rather than assuming any
particular range is safe.

**Clean vs. investigate:**
- **Clean:** every source IP is either `127.0.0.1`/`::1` (cowork.main /
  localhost calls), your own known IP(s) from step above, or timestamps that
  line up exactly with when you were actively using claude.ai.
- **Investigate:** any IP you can't attribute to the above, especially if
  it made calls to mutating tools (`delete_transaction`,
  `update_transactions_bulk`, `control_home`, `create_gmail_draft`,
  `close_linear_issue`, `create_scheduled_transaction`) rather than only
  read tools — check what it actually called (only possible if nginx logs
  the request body / if you add request logging going forward — see note
  below), and treat any unexplained mutating call as an incident, not a log
  curiosity.

**Recommendation, not required for this deploy:** since there's currently no
app-level access log, consider adding minimal request logging (tool name +
source IP + timestamp, not full payloads) so a future audit doesn't hit this
same gap.

---

## 6. Token rotation checklist (only if the audit in step 5 is dirty)

Rotate in this order — YNAB and Gmail first since those touch money and
your inbox directly, then Linear/HA/Airtable. For each: generate the new
credential, update the box's env, restart, verify, **then** revoke the old
one (don't revoke before confirming the new one works — you'll lock
yourself out of the same audit trail you're trying to protect).

| # | Provider | Env var(s) (in `$REPO_DIR/mcp/.env`) | Revoke/rotate here |
|---|---|---|---|
| 1 | **YNAB** | `YNAB_API_KEY` | https://app.ynab.com/settings/developer — delete the old Personal Access Token, generate a new one |
| 2 | **Gmail** | `GMAIL_USER`, `GMAIL_APP_PASSWORD` | https://myaccount.google.com/apppasswords — revoke the old App Password, generate a new one (requires 2FA enabled on the account) |
| 3 | **Linear** | `LINEAR_API_KEY` | https://linear.app/settings/api — revoke the old API key, create a new one |
| 4 | **Home Assistant** | `HA_URL`, `HA_REFRESH_TOKEN`, `HA_CLIENT_ID` | in your own HA instance: profile (bottom-left avatar) → Security → Long-Lived Access Tokens — delete the old token, create a new one. If this used a full OAuth client (`HA_CLIENT_ID`) rather than a long-lived token, also check Settings → Devices & Services → your integration's application credentials. |
| 5 | **Airtable** | `AIRTABLE_API_KEY` | https://airtable.com/create/tokens — delete the old personal access token, create a new one with the same scopes/base access |

After each rotation:
```bash
# edit $REPO_DIR/mcp/.env with the new value, then:
pm2 restart homeos-mcp
pm2 logs homeos-mcp --lines 20 --nostream   # confirm no auth errors for that source
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:$MCP_PORT/health | python3 -m json.tool
# confirm the relevant source shows "ok": true and a fresh last_updated
```

Also rotate `BEARER_TOKEN` itself if you have any reason to think it leaked
(not just if YNAB/Linear/etc. look dirty) — regenerate per step 0, update
both the MCP server's `.env` and `cowork.main`'s config (step 2), and update
the claude.ai connector config to match.

---

## 7. Home Assistant TLS diagnostics (carried over from DIAGNOSIS.md)

Run these on the box itself — this session couldn't reach your home network
to run them remotely:

```bash
# 1. Is the endpoint reachable at all, and what does the TLS handshake look like?
curl -v --max-time 10 "$HA_URL/api/config" -H "Authorization: Bearer <token>"

# 2. Cert expiry / chain
HOST=$(echo "$HA_URL" | sed -E 's#https?://##; s#/.*##; s#:.*##')
PORT=443
echo | openssl s_client -connect "$HOST:$PORT" -servername "$HOST" 2>/dev/null | openssl x509 -noout -dates -subject -issuer

# 3. Does the tunnel/DNS entry resolve, and to where?
dig +short "$HOST"
nslookup "$HOST"

# 4. Is the tunnel process actually alive?
systemctl status cloudflared 2>/dev/null || pm2 status || docker ps | grep -i tunnel
```

"Client network socket disconnected before secure TLS connection was
established" means the TCP connection closed *before* the TLS handshake
completed — check the tunnel process first (crashed/restarted/expired
credential), then a reverse-proxy SNI/Host mismatch, then firewall/NAT/idle
reaping, then stale DNS. Full reasoning in DIAGNOSIS.md.

---

## 8. `briefs/` directory permissions

```bash
chmod 700 "$REPO_DIR/briefs"
ls -ld "$REPO_DIR/briefs"   # confirm owner-only (drwx------)
```
Confirm the owner is whatever user actually runs `pm2`/`node server.js` —
if that's a shared account, this is still worth doing but note it in your
own security notes as a shared-account caveat.

---

## Done-when

- [ ] Step 1: `BEARER_TOKEN` confirmed set before any restart.
- [ ] Step 2: `cowork.main` confirmed to send (or updated to send) the bearer token.
- [ ] Step 3: deployed, service restarted, came up `online` in `pm2 status`.
- [ ] Step 4: all four curl checks match expected status codes; a real/dry brief run completed successfully afterward.
- [ ] Step 5: log audit done, classified clean or investigate.
- [ ] Step 6: only if step 5 was dirty.
- [ ] Step 7: HA diagnostic output captured (root cause may still be open — that's fine, this just needs to have been run).
- [ ] Step 8: `briefs/` is `700`.
