# Okta Staff PSE (Reviews) — 2-Week Secure Code Review Prep

**Goal:** build speed and fluency at manual secure code review under time
pressure, narrated aloud, weighted toward identity/auth bug classes
(token validation, signature verification, JWT, redirect/callback handling,
session management, authz, SSRF) across Java, Go, and Python.

**Format per day (~45 min):**
1. 5 min — read the snippet cold, no notes
2. 10 min — silent annotation: mark every line you'd flag, jot *why*
3. 15 min — **narrate aloud** as if in the interview: walk the code,
   state the vuln, answer the "reviewer follow-ups" listed in the day file
4. 10 min — write the fix (real code, not just prose)
5. 5 min — open the `*-answer.md` file, self-grade, note gaps

Don't open the answer key before step 5. The point is producing the
finding under your own steam, out loud, before you see it confirmed.

**Rule of thumb for narration:** for every bug, hit four beats explicitly —
*what's wrong → why it's exploitable (concrete attacker steps) → how you'd
fix it → what you'd check next (blast radius / related patterns elsewhere
in the codebase)*. That last beat is the one people skip and it's the one
that reads as "staff-level" — it shows you think in terms of systemic risk,
not just the one diff.

## Schedule

| Day | Lang | Topic | CWE | Difficulty |
|-----|------|-------|-----|------------|
| 1 | Java | JWT algorithm confusion (RS256/HS256 key reuse) | CWE-347 | 1 |
| 2 | Go | OAuth open redirect via unchecked post-login `redirect_uri` | CWE-601 | 1 |
| 3 | Python | SAML signature verification bypass (assertion not bound to signed reference) | CWE-347 | 2 |
| 4 | Java | Session fixation (session ID not rotated on login) | CWE-384 | 2 |
| 5 | Go | SSRF via user-supplied webhook/callback URL fetch | CWE-918 | 2 |
| 6 | Python | IDOR / missing object-level authz check | CWE-639 | 2 |
| 7 | Java | JWT verification skips expiry/audience/issuer checks | CWE-347 / CWE-295 | 3 |
| 8 | Go | Missing/predictable OAuth `state` (CSRF on auth code flow) | CWE-352 | 3 |
| 9 | Python | Redirect URI allowlist bypass via prefix/substring match | CWE-601 | 3 |
| 10 | Java | Insecure deserialization of session/token data | CWE-502 | 4 |
| 11 | Go | Non-constant-time secret/token comparison (timing side channel) | CWE-208 | 4 |
| 12 | Python | PKCE not enforced for public client (downgrade) | CWE-287 | 4 |
| 13 | Go | JWKS `kid`/`jku` header trusted → SSRF to attacker-controlled key set | CWE-918 / CWE-347 | 5 |
| 14 | Python | **Final mock:** multi-file OIDC relying-party diff, 4-6 planted bugs, 30 min timed, graded rubric | mixed | 5 |

Each day ships as `dayNN.md` (the exercise) and `dayNN-answer.md` (hidden
answer key — don't peek early). Snippets are original code written to
exercise patterns seen in real identity-library CVEs and advisories; none
are copied from the actual vulnerable projects.

Days are generated one at a time as you work through them — say "next day"
when you're ready and I'll drop the next file in.
