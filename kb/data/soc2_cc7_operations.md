# SOC 2 Trust Service Criteria: CC7 - System Operations

Source: AICPA Trust Services Criteria (2017, updated 2022)

---

## CC7.1 - Detection and Monitoring of New Vulnerabilities

The entity uses detection and monitoring procedures to identify (1) changes to configurations that result in the introduction of new vulnerabilities, and (2) susceptibilities to newly discovered vulnerabilities.

**Control Requirements:**

- Continuously monitors for new vulnerabilities in deployed software and dependencies.
- Subscribes to security advisories (NVD, vendor advisories, CVE feeds).
- Remediates critical and high vulnerabilities within defined SLAs (e.g., critical: 15 days, high: 30 days).
- Performs periodic vulnerability assessments.

**Evidence in Code/Infrastructure:**

- Automated dependency scanning (Dependabot, Renovate, Snyk) with alerts enabled.
- Trivy or equivalent scanning container images in CI.
- CHANGELOG or patch notes documenting vulnerability remediations.
- Pinned dependency versions enabling reproducible builds and auditability.

**Common Gaps:**

- No automated vulnerability scanning in CI pipeline.
- Critical CVEs present in transitive dependencies with no remediation plan.
- Docker images built from unpatched base images.
- No SLA documentation for vulnerability remediation.

---

## CC7.2 - Monitoring Infrastructure and Software for Anomalous Activity

The entity monitors the system components and the operation of those controls on an ongoing basis to identify (1) potential security events, and (2) anomalies that are indicative of malicious acts, natural disasters, and errors affecting the entity's ability to meet its objectives.

**Control Requirements:**

- Logs security-relevant events: authentication attempts, access to sensitive data, privilege escalation, configuration changes.
- Alerts on anomalous patterns (multiple failed logins, off-hours admin access, large data exports).
- Retains logs for a period sufficient to support investigations (typically 12 months).
- Protects logs from tampering (append-only log stores, SIEM ingestion).

**Evidence in Code:**

- Structured logging of authentication events (success and failure) with user ID and IP.
- Audit trail for sensitive operations (data exports, admin actions, permission changes).
- Log entries that do not include sensitive data (passwords, full credit card numbers, SSNs).
- Centralized log shipping configuration (CloudWatch, Datadog, Splunk).

**Common Gaps:**

- Authentication failures not logged (no visibility into brute-force attempts).
- Sensitive data (passwords, tokens, PII) included in log entries.
- No audit trail for privileged operations.
- Logs written only locally (lost on container restart, not centrally collected).

---

## CC7.3 - Evaluation and Response to Security Events

Detected security events are evaluated to determine whether they represent security incidents and, if so, how to respond.

**Control Requirements:**

- Defines what constitutes a security incident (vs. a non-incident event).
- Establishes an incident response plan with defined roles and escalation paths.
- Documents and tracks incidents to closure.
- Performs post-incident reviews for significant events.

**Evidence in Code:**

- SECURITY.md or incident response runbook present in the repository.
- Error handling that does not leak stack traces or internal details to external users.
- Automated alerts wired to on-call systems (PagerDuty, OpsGenie).

**Common Gaps:**

- Stack traces or internal error details returned in API responses to clients.
- No SECURITY.md or vulnerability disclosure policy.
- Exception handlers that swallow errors silently with no logging.
