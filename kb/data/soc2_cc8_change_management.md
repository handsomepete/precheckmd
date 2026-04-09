# SOC 2 Trust Service Criteria: CC8 - Change Management

Source: AICPA Trust Services Criteria (2017, updated 2022)

---

## CC8.1 - Change Management Process

The entity authorizes, designs, develops or acquires, configures, documents, tests, approves, and implements changes to infrastructure, data, software, and procedures to meet its change management objectives.

**Control Requirements:**

- All changes to production systems go through a defined change management process.
- Changes are reviewed and approved before deployment (code review, change advisory board for major changes).
- Changes are tested before promotion to production (unit tests, integration tests, staging environment).
- Emergency changes have a defined expedited process and require post-hoc review.
- Changes are documented with a description, testing approach, and rollback plan.

**Evidence in Code:**

- Pull request (PR) or merge request (MR) workflow enforced (no direct commits to main/master).
- Required code reviews before merge (branch protection rules).
- CI pipeline runs tests on every PR.
- Semantic versioning and CHANGELOG maintained.
- Deployment scripts or infrastructure-as-code (Terraform, Ansible) in version control.
- Rollback procedures documented (e.g., blue/green deployments, feature flags).

**Common Gaps:**

- Force pushes enabled on main branch (history can be rewritten without review).
- No branch protection or required reviews.
- CI pipeline skippable by committers (tests not mandatory).
- Infrastructure changes made directly in cloud console (no IaC, no audit trail).
- No CHANGELOG or release notes.
- Deployment process not documented or automated.

---

## CC8.2 - Configuration Management

Infrastructure and software configurations are maintained in a controlled state and changes are tracked.

**Control Requirements:**

- Baseline configurations are defined and documented.
- Deviations from baseline configurations are detected and remediated.
- Configuration changes require authorization and are logged.
- Secrets and environment-specific configuration are managed separately from code.

**Evidence in Code:**

- Infrastructure-as-code (IaC) committed to version control for all environments.
- Environment-specific values managed via environment variables or secrets managers, not committed to repo.
- .gitignore excludes .env files and credential files.
- Docker Compose or Kubernetes manifests pinned to specific image versions.
- README or runbooks document how to configure the service.

**Common Gaps:**

- .env files committed to the repository (even if later removed from history via git).
- Configuration drift between environments (dev vs. staging vs. production).
- Undocumented manual steps required to configure production.
- Docker images tagged `:latest` (non-deterministic builds, hard to audit what version is running).
