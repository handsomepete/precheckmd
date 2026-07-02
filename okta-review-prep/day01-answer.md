# Day 1 — Answer Key

## The bug

`verify()` lets the **token itself** (via the attacker-controlled `alg`
header) choose which verification path runs. When `alg` is `HS256`, the
code treats the RSA **public** key's raw bytes as an HMAC **secret**.

That's the classic RS256/HS256 **algorithm confusion** attack, first
publicized broadly around 2015 (Auth0's writeup, CVE-2015-2951 in an
early `node-jsonwebtoken`, and the same shape recurred in PyJWT
CVE-2016-10555 and other libraries). It shows up wherever a library or
hand-rolled verifier dispatches on the header instead of on
caller-supplied, out-of-band configuration.

## Why it's exploitable — concrete steps

1. The IdP's RSA **public** key is, by design, public — it's published in
   a JWKS document (`/.well-known/jwks.json` or similar) precisely so
   relying parties can fetch it. An attacker gets it the same way any
   legitimate verifier would.
2. Attacker crafts a JWT header `{"alg":"HS256","typ":"JWT"}` and any
   claims they want (`sub`, `roles`, `exp` far in the future, etc.).
3. Attacker computes `HMAC-SHA256(signingInput, publicKeyBytes)` — they
   have both required inputs, since the public key bytes are exactly
   what `idpPublicKey.getEncoded()` will hand to `verifyHmac` at
   verification time.
4. They append that HMAC as the JWT signature. `verify()` looks at the
   header, dispatches to `verifyHmac`, recomputes the same HMAC using
   the same public key bytes, and gets a match. Forged token accepted
   with arbitrary claims.

**Precondition:** essentially none — this always holds as long as the
public key is discoverable (which it is, by design, for any RS256-based
IdP) and the "support both algorithms" branch exists. There's no scenario
where an attacker needs insider access; this is a pure remote
unauthenticated forgery once they see one legitimate RS256 token or the
JWKS endpoint.

## The fix

The core principle: **the verifier, not the token, decides which
algorithm and which key are used.** Never branch on `header.alg`.

```java
public Claims verify(String token) throws JwtException {
    String[] parts = token.split("\\.");
    if (parts.length != 3) {
        throw new JwtException("Malformed token");
    }

    Header header = parseHeader(decode(parts[0]));
    if (!"RS256".equals(header.getAlg())) {
        throw new JwtException("Unexpected algorithm: " + header.getAlg());
    }

    String signingInput = parts[0] + "." + parts[1];
    byte[] signature = Base64.getUrlDecoder().decode(parts[2]);

    if (!verifyRsa(signingInput, signature, idpPublicKey)) {
        throw new JwtException("Invalid signature");
    }

    return Claims.parse(decode(parts[1]));
}
```

Notes on the fix:
- Hard-code the expected algorithm (or accept a fixed allowlist passed in
  at construction time, e.g. `EnumSet.of(RS256)`), never derive trust from
  the token's own header.
- If HS256 service-to-service tokens are a real requirement, that needs a
  **separate verifier instance constructed with a distinct HMAC secret**,
  used only for that trust boundary — not the same verifier silently
  accepting either.
- Bonus hardening: also reject/ignore a `none` algorithm defensively (this
  code already does, since it's not in the switch — good), and prefer a
  maintained library (Nimbus JOSE+JWT, `java-jwt`) configured with an
  explicit allowed-algorithms list rather than hand-rolled dispatch —
  hand-rolled JWT code is a recurring source of exactly this bug class.

## What to check next (the "staff-level" beat)

- **Every other verifier in the codebase** — is this pattern copy-pasted
  service-to-service? Grep for `getAlg()`, `"HS256"`, `Mac.getInstance`.
- **Is the public key actually reused as a would-be HMAC key anywhere
  else** — key material boundaries (asymmetric vs. symmetric) should
  never cross; check config/secrets management for any shared blob.
- **JWKS endpoint exposure** — confirm it's meant to be public (it should
  be), and check `kid` handling elsewhere in the codebase for a related
  bug class (Day 13 territory: trusting `kid`/`jku` to select which key
  material to fetch/use).
- **Are `exp`/`aud`/`iss` actually validated** downstream? A signature fix
  alone doesn't help if claims validation is also missing or weak (that's
  Day 7's bug — worth flagging as a "look at this next" even if it's out
  of scope for this diff).
- **Library version** — if this ever gets replaced with a library call
  instead of hand-rolled code, check the library's changelog for known
  algorithm-confusion CVEs and confirm the configured algorithm allowlist
  is enforced, not just "supported."

## Self-grading rubric

- [ ] Named the bug class (algorithm confusion / signature verification
      bypass) without prompting
- [ ] Explained *why* the public key being public matters — not just
      "it uses the wrong algorithm"
- [ ] Produced concrete attacker steps (forge header → compute HMAC with
      public key → forged token accepted)
- [ ] Gave a real fix (pin algorithm, don't branch on header) — not just
      "validate better"
- [ ] Raised at least one "check next" item unprompted

4-5 checked: strong, interview-ready. 2-3: solid instinct, work on the
narration discipline (say the four beats explicitly even when it feels
obvious). 0-1: reread the JWT signing/verification model before Day 2.
