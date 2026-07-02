# Day 1 — JWT Algorithm Confusion (Java)

**CWE-347: Improper Verification of Cryptographic Signature**
Difficulty: 1/5 · Time budget: 45 min · Language: Java

**Context you'd be given in the interview:** this is the token verifier for
an internal service that accepts JWTs issued by your identity provider. The
IdP signs tokens with RS256 using a private key; the corresponding public
key is published (as IdPs do — think JWKS). A teammate wrote this verifier
last quarter. You're reviewing it as part of a broader auth-hardening pass.

```java
import java.security.PublicKey;
import java.security.Signature;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import java.util.Base64;
import java.nio.charset.StandardCharsets;

public class JwtVerifier {

    private final PublicKey idpPublicKey;

    public JwtVerifier(PublicKey idpPublicKey) {
        this.idpPublicKey = idpPublicKey;
    }

    /**
     * Verifies a compact-serialized JWT and returns its claims.
     * Throws JwtException if the signature is invalid or the token is malformed.
     */
    public Claims verify(String token) throws JwtException {
        String[] parts = token.split("\\.");
        if (parts.length != 3) {
            throw new JwtException("Malformed token");
        }

        Header header = parseHeader(decode(parts[0]));
        String signingInput = parts[0] + "." + parts[1];
        byte[] signature = Base64.getUrlDecoder().decode(parts[2]);

        boolean valid;
        switch (header.getAlg()) {
            case "RS256":
                valid = verifyRsa(signingInput, signature, idpPublicKey);
                break;
            case "HS256":
                // Some of our older internal services still mint HS256
                // tokens for service-to-service calls, so we support both.
                valid = verifyHmac(signingInput, signature, idpPublicKey.getEncoded());
                break;
            default:
                throw new JwtException("Unsupported algorithm: " + header.getAlg());
        }

        if (!valid) {
            throw new JwtException("Invalid signature");
        }

        return Claims.parse(decode(parts[1]));
    }

    private boolean verifyRsa(String signingInput, byte[] signature, PublicKey key) {
        try {
            Signature sig = Signature.getInstance("SHA256withRSA");
            sig.initVerify(key);
            sig.update(signingInput.getBytes(StandardCharsets.UTF_8));
            return sig.verify(signature);
        } catch (Exception e) {
            return false;
        }
    }

    private boolean verifyHmac(String signingInput, byte[] signature, byte[] keyBytes) {
        try {
            Mac mac = Mac.getInstance("HmacSHA256");
            mac.init(new SecretKeySpec(keyBytes, "HmacSHA256"));
            byte[] expected = mac.doFinal(signingInput.getBytes(StandardCharsets.UTF_8));
            return java.util.Arrays.equals(expected, signature);
        } catch (Exception e) {
            return false;
        }
    }

    private String decode(String b64url) {
        return new String(Base64.getUrlDecoder().decode(b64url), StandardCharsets.UTF_8);
    }
}
```

`Header` and `Claims` are simple POJOs with a JSON-backed `parse(String)`
factory — assume unremarkable, standard parsing.

## What to do

Work through the 45-min format from `00-overview.md`. In particular, make
sure your narration explicitly answers:

1. **Why is this exploitable?** Walk through the concrete steps an attacker
   takes to forge a token this code will accept as valid.
2. **What has to be true about the deployment for the attack to work** —
   is there any precondition, or does it always hold?
3. **How would you fix it**, at the code level? Give the actual diff you'd
   ask for.
4. **What would you check next?** Think beyond this one file — what related
   code, config, or library defaults would you go look at in the same repo
   or the same team's other services?

When you're ready, open `day01-answer.md`.
