# SOC 2 Trust Service Criteria: CC6 - Logical and Physical Access Controls

Source: AICPA Trust Services Criteria (2017, updated 2022)
Relevance: These criteria are evaluated when auditing software systems for SOC 2 Type I/II compliance.

---

## CC6.1 - Logical Access Security Software, Infrastructure, and Architectures

The entity implements logical access security software, infrastructure, and architectures over protected information assets to protect them from security events to meet the entity's objectives.

**Control Requirements:**

- Identifies and manages the inventory of information assets (data, software, hardware, people).
- Restricts logical access to information assets to authorized users through identification and authentication controls.
- Considers network segmentation, firewalls, and intrusion detection systems.
- Manages points of access including network, application, and data layer controls.

**Evidence Commonly Reviewed in Code/Infrastructure Audits:**

- Authentication middleware enforcing session tokens, API keys, or OAuth flows.
- Authorization checks verifying user permissions before resource access (RBAC/ABAC patterns).
- Absence of hardcoded credentials or secrets in source code.
- Use of secrets management (environment variables, vaults) rather than inline secrets.
- Network policy definitions (Kubernetes NetworkPolicy, security groups, VPC rules).

**Common Gaps Found in Repositories:**

- API endpoints accessible without authentication (missing auth middleware).
- Hardcoded API keys, database passwords, or tokens in code or config files.
- Overly permissive CORS policies (e.g., `Access-Control-Allow-Origin: *` on authenticated endpoints).
- No rate limiting on authentication endpoints (brute-force vulnerability).
- SQL queries built by string concatenation (potential injection leading to unauthorized access).

---

## CC6.2 - New User, Internal User, and User Credential Enrollment

Prior to issuing system credentials and granting system access, the entity registers and authorizes new internal and external users whose access is administered by the entity. Methods of authentication are commensurate with the risk associated with the type of access.

**Control Requirements:**

- Formally registers users before granting access.
- Assigns unique identifiers to each user.
- Uses multi-factor authentication (MFA) for privileged or remote access.
- Manages password policies (complexity, expiration, history).

**Evidence in Code:**

- Password hashing with strong algorithms (bcrypt, Argon2, scrypt). Absence of MD5/SHA1 for passwords.
- MFA enrollment flows in authentication code.
- Password reset flows that expire tokens and validate identity.
- Admin or privileged role assignment requiring elevated approval flows.

**Common Gaps:**

- Passwords stored as plain text or with weak hashes (MD5, SHA1).
- Password reset links that do not expire.
- No MFA implementation for admin accounts.
- User enumeration possible through login/reset error messages.

---

## CC6.3 - Removal of Access

The entity authorizes, modifies, or removes access to data, software, functions, and other protected information assets based on roles, responsibilities, or the system design and changes, giving consideration to the concepts of least privilege and segregation of duties to meet the entity's objectives.

**Control Requirements:**

- Implements principle of least privilege (users have only the minimum access needed).
- Removes or revokes access promptly upon role change or termination.
- Reviews access rights periodically (access reviews/recertification).
- Segregates duties for sensitive operations (no single user can initiate and approve).

**Evidence in Code:**

- Role-based access control (RBAC) with clearly defined roles and permission sets.
- No single admin "super user" account bypassing all permission checks.
- Audit logging of permission changes.
- Automated deprovisioning hooks (e.g., triggered on HR system events).

**Common Gaps:**

- Single global admin role with no granular permissions.
- No audit log of who granted or revoked access.
- Access tokens or API keys that never expire.
- Service accounts with production-level permissions used in development.

---

## CC6.6 - Logical Access Security Over Applications

Logical access security measures are implemented to protect against threats from sources outside the system boundaries.

**Control Requirements:**

- Protects against external attacks (injection, XSS, CSRF, broken authentication).
- Validates and sanitizes all input at system boundaries.
- Encrypts sensitive data in transit (TLS 1.2+) and at rest (AES-256).
- Implements web application firewall (WAF) or equivalent.

**Evidence in Code:**

- Input validation and sanitization at all user-facing entry points.
- Use of parameterized queries or ORM (never raw string concatenation for SQL).
- CSRF token implementation in state-changing web forms.
- Content Security Policy (CSP) headers.
- TLS configuration (no TLS 1.0/1.1, valid certificates).
- Encryption of PII and sensitive data fields at rest.

**Common Gaps:**

- String-interpolated SQL queries (SQLi risk).
- User-supplied content rendered without escaping (XSS risk).
- Missing CSRF protection on state-changing endpoints.
- Sensitive data (SSN, credit card, passwords) logged in plaintext.
- HTTP used instead of HTTPS for API endpoints.
- Outdated dependencies with known CVEs.

---

## CC6.7 - Transmission of Data

Transmission of data using public networks is encrypted using TLS 1.2 or higher. Data is protected from unauthorized access during transmission.

**Control Requirements:**

- Encrypts all data in transit using current TLS standards (minimum TLS 1.2).
- Disables deprecated protocols (SSL 3.0, TLS 1.0, TLS 1.1).
- Validates TLS certificates and does not disable certificate verification.
- Protects API keys and tokens during transmission.

**Evidence in Code:**

- HTTP client configurations that enforce TLS and verify certificates.
- No `verify=False` or `SSL_VERIFY=0` in production configurations.
- HTTPS-only enforced via HSTS headers or redirect rules.
- Sensitive data not passed in URL query parameters (logged by servers/proxies).

**Common Gaps:**

- TLS certificate verification disabled in HTTP clients.
- Sensitive tokens or credentials passed as URL query parameters.
- Internal service-to-service communication over plain HTTP.
- HSTS not configured on public-facing services.

---

## CC6.8 - Controls to Prevent or Detect Unauthorized or Malicious Software

The entity implements controls to prevent or detect and act upon the introduction of unauthorized or malicious software to meet the entity's objectives.

**Control Requirements:**

- Scans code for vulnerabilities and malicious patterns in CI/CD pipelines.
- Manages and pins software dependencies; monitors for known CVEs.
- Controls what software can be installed in production environments.
- Reviews and approves open-source software before use.

**Evidence in Code:**

- Dependency lock files (package-lock.json, Pipfile.lock, poetry.lock, go.sum).
- Software composition analysis (SCA) in CI pipeline (Dependabot, Snyk, Trivy).
- SAST tools (Semgrep, Bandit, SonarQube) in CI pipeline.
- No use of packages with known critical CVEs (checked against NVD/OSV).
- Pinned Docker base image digests (not `:latest` tags).

**Common Gaps:**

- Unpinned or floating dependency versions (`>=1.0.0` with no upper bound).
- Dependencies with known critical/high CVEs.
- No SAST or SCA in CI pipeline.
- Docker images using `:latest` tags (non-reproducible builds).
- Secrets or malware signatures detected by gitleaks/truffleHog.
