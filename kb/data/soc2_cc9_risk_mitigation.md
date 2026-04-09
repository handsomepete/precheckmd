# SOC 2 Trust Service Criteria: CC9 - Risk Mitigation

Source: AICPA Trust Services Criteria (2017, updated 2022)

---

## CC9.1 - Risk Identification and Assessment

The entity identifies, selects, and develops risk mitigation activities for risks arising from potential business disruptions.

**Control Requirements:**

- Identifies risks to the availability and security of the system.
- Assesses the likelihood and impact of identified risks.
- Selects controls to reduce risks to acceptable levels.
- Documents the risk assessment process and results.

**Evidence in Code:**

- Documented threat model or security architecture review for the system.
- SECURITY.md describing known risks and mitigations.
- Implementation of resilience patterns (retries with backoff, circuit breakers, timeouts on outbound calls).
- Graceful degradation paths when dependencies fail.

**Common Gaps:**

- No documented threat model or risk register.
- External API calls with no timeouts (can cause indefinite hangs).
- No circuit breaker or retry logic for critical dependencies.
- Single points of failure with no documented mitigation.

---

## CC9.2 - Vendor and Third-Party Risk Management

The entity assesses and manages risks associated with vendors and business partners whose products and services are part of the system's processing environment.

**Control Requirements:**

- Maintains an inventory of third-party vendors and open-source components.
- Assesses the security posture of vendors handling sensitive data.
- Includes security requirements in vendor contracts (DPA, BAA for PHI, SLAs).
- Monitors vendors for security incidents affecting the entity's data.

**Evidence in Code:**

- Software Bill of Materials (SBOM) or equivalent dependency inventory.
- Open-source license compliance (no GPL in commercial closed-source products).
- Dependencies pinned to specific versions (not floating ranges) to enable reproducibility.
- Provenance of Docker base images (using official or verified publisher images).
- Third-party integrations use least-privilege API scopes/permissions.

**Common Gaps:**

- No SBOM or dependency inventory.
- GPL-licensed dependencies in commercial closed-source code (license risk).
- Third-party OAuth integrations requesting overly broad scopes.
- No documented process for evaluating new open-source dependencies.
- Supply chain vulnerabilities: packages fetched from PyPI/npm without integrity checks.
