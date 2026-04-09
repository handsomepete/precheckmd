"""SOC 2 compliance report agent.

Runs inside the nox-sandbox container. Performs a multi-step audit of a
GitHub repository against SOC 2 Trust Service Criteria using the Anthropic
Messages API tool-use loop, then writes:
  - findings.json  : structured per-control assessment
  - report.pdf     : rendered compliance report (Step 6 full impl)

Token budget and transcript logging enforced throughout the loop.
"""

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import anthropic

from tools.context import MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS, SCRATCH_DIR
from tools.git_clone import git_clone
from tools.query_kb import query_kb
from tools.read_file import read_file
from tools.render_pdf import render_pdf
from tools.run_gitleaks import run_gitleaks
from tools.run_semgrep import run_semgrep
from tools.write_artifact import write_artifact

logger = logging.getLogger(__name__)

# Default model; override with AGENT_MODEL env var.
# Sonnet 4.5 is used (not Opus): it is faster, cheaper, and fits comfortably
# within the 200k/100k token budget for a typical audit. Opus would burn the
# output budget in fewer turns on a medium-sized repo.
_DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
_MODEL = os.environ.get("AGENT_MODEL", _DEFAULT_MODEL)

# Max tokens per individual API response (caps a single turn, not total)
_MAX_TOKENS_PER_TURN = 8192

# Truncate tool result strings beyond this length before sending to the API
_TOOL_RESULT_MAX_CHARS = 80_000


# ---------------------------------------------------------------------------
# Token budget
# ---------------------------------------------------------------------------

class TokenBudgetExceeded(RuntimeError):
    pass


class TokenBudget:
    def __init__(self, max_input: int, max_output: int) -> None:
        self.max_input = max_input
        self.max_output = max_output
        self.input_used = 0
        self.output_used = 0

    def record(self, input_tokens: int, output_tokens: int) -> None:
        self.input_used += input_tokens
        self.output_used += output_tokens
        logger.info(
            "tokens: input %d/%d  output %d/%d",
            self.input_used, self.max_input,
            self.output_used, self.max_output,
        )
        if self.input_used > self.max_input:
            raise TokenBudgetExceeded(
                f"Input token budget exceeded: {self.input_used} > {self.max_input}"
            )
        if self.output_used > self.max_output:
            raise TokenBudgetExceeded(
                f"Output token budget exceeded: {self.output_used} > {self.max_output}"
            )

    def remaining_output(self) -> int:
        return max(256, self.max_output - self.output_used)


# ---------------------------------------------------------------------------
# Transcript logger
# ---------------------------------------------------------------------------

class TranscriptLogger:
    """Logs every message and tool call to agent_transcripts and a local JSON file."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.sequence = 0
        self._db = None
        self._records: list[dict] = []
        self._init_db()

    def _init_db(self) -> None:
        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            return
        try:
            from db.session import SyncSessionLocal
            self._db = SyncSessionLocal()
        except Exception as exc:
            # TODO: in production this should raise, not warn. Full agent transcript
            # per job is part of the trust story for PreCheckMD customers; silently
            # losing it is a product-level failure, not just a logging gap.
            logger.warning("TranscriptLogger: DB unavailable: %s", exc)

    def log(self, role: str, content_type: str, content: dict) -> None:
        seq = self.sequence
        self.sequence += 1
        record = {
            "job_id": self.job_id,
            "sequence": seq,
            "role": role,
            "content_type": content_type,
            "content": content,
        }
        self._records.append(record)

        if self._db is not None:
            try:
                from db.models import AgentTranscript
                row = AgentTranscript(
                    id=str(uuid.uuid4()),
                    job_id=self.job_id,
                    sequence=seq,
                    role=role,
                    content_type=content_type,
                    content=content,
                )
                self._db.add(row)
                self._db.commit()
            except Exception as exc:
                logger.warning("TranscriptLogger: failed to write DB row: %s", exc)

    def flush_to_file(self) -> None:
        """Write the full transcript to a JSON artifact."""
        try:
            write_artifact(
                "transcript.json",
                json.dumps(self._records, indent=2, default=str),
                mime_type="application/json",
            )
        except Exception as exc:
            logger.warning("TranscriptLogger: failed to write transcript file: %s", exc)

    def close(self) -> None:
        self.flush_to_file()
        if self._db is not None:
            try:
                self._db.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Tool definitions (schema shown to Claude)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "git_clone",
        "description": (
            "Clone a public GitHub repository (HTTPS only) into the scratch directory. "
            "Use depth=1 shallow clone. Returns the absolute path to the cloned repo. "
            "Call this first before any other analysis tool."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo_url": {
                    "type": "string",
                    "description": "HTTPS URL of the repository, e.g. https://github.com/org/repo",
                },
                "dest_name": {
                    "type": "string",
                    "description": "Subdirectory name inside the scratch dir (default: 'repo').",
                },
            },
            "required": ["repo_url"],
        },
    },
    {
        "name": "run_semgrep",
        "description": (
            "Run Semgrep static analysis on a directory. Returns a dict with 'findings' "
            "(list of {rule_id, path, line, message, severity}) and 'stats'. "
            "Use config='auto' for language-aware rules or a specific ruleset like 'p/owasp-top-ten'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_dir": {
                    "type": "string",
                    "description": "Absolute path to the directory to scan.",
                },
                "config": {
                    "type": "string",
                    "description": "Semgrep rule config (default: 'auto').",
                },
            },
            "required": ["target_dir"],
        },
    },
    {
        "name": "run_gitleaks",
        "description": (
            "Scan a repository for hardcoded secrets and credentials using Gitleaks. "
            "Returns a dict with 'findings' (list of {rule_id, file, line, secret_partial, "
            "description}) and 'total'. Secret values are redacted to a partial preview."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "target_dir": {
                    "type": "string",
                    "description": "Absolute path to the repository to scan.",
                },
            },
            "required": ["target_dir"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a file or list a directory from the scratch directory. "
            "Path is relative to the scratch dir (e.g. 'repo/package.json'). "
            "Returns file contents as a string, truncated at max_bytes if large. "
            "If given a directory path, returns a listing of its contents."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "relative_path": {
                    "type": "string",
                    "description": "Path relative to the scratch directory.",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Maximum bytes to return (default 50000).",
                },
            },
            "required": ["relative_path"],
        },
    },
    {
        "name": "query_kb",
        "description": (
            "Search the knowledge base for SOC 2 (and other compliance framework) "
            "guidance relevant to a topic. Returns a list of matching chunks with "
            "title, body, and similarity score. Use this to look up control requirements "
            "before assessing a specific criterion."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Natural language query, e.g. 'SOC 2 password hashing requirements'.",
                },
                "source": {
                    "type": "string",
                    "description": "Filter by source: 'soc2', 'hipaa', 'owasp_asvs'. Omit to search all.",
                },
                "top_k": {
                    "type": "integer",
                    "description": "Number of results to return (default 5, max 20).",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "write_artifact",
        "description": (
            "Write a file to the job artifact directory. Use this to persist "
            "findings.json as a downloadable artifact for the client. "
            "Note: render_pdf receives findings directly as an argument; it does "
            "NOT read findings.json from disk. So write_artifact and render_pdf "
            "are independent: write_artifact produces a JSON artifact, render_pdf "
            "produces the PDF artifact. Call both. Returns the absolute path."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "Output filename (no path separators), e.g. 'findings.json'.",
                },
                "content": {
                    "type": "string",
                    "description": "File content as a string.",
                },
                "mime_type": {
                    "type": "string",
                    "description": "MIME type (default: 'text/plain').",
                },
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "render_pdf",
        "description": (
            "Render the final compliance report PDF and save it as an artifact. "
            "Call this as the LAST step after all findings are compiled. "
            "Returns the path to the generated PDF artifact."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Report title, e.g. 'SOC 2 Compliance Report - org/repo'.",
                },
                "findings": {
                    "type": "array",
                    "description": (
                        "List of control assessment objects. Each must have: "
                        "control (str), title (str), status ('pass'|'fail'|'partial'|'unknown'), "
                        "evidence (str), gaps (str), recommendation (str), severity (str)."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "control": {"type": "string"},
                            "title": {"type": "string"},
                            "status": {"type": "string", "enum": ["pass", "fail", "partial", "unknown"]},
                            "evidence": {"type": "string"},
                            "gaps": {"type": "string"},
                            "recommendation": {"type": "string"},
                            "severity": {
                                "type": "string",
                                "enum": ["critical", "high", "medium", "low", "info"],
                            },
                        },
                        "required": ["control", "title", "status", "evidence"],
                    },
                },
                "metadata": {
                    "type": "object",
                    "description": "Optional metadata: repo_url, scan_date, job_id.",
                },
            },
            "required": ["title", "findings"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def _dispatch_tool(name: str, tool_input: dict[str, Any]) -> Any:
    """Execute a tool by name and return the result."""
    if name == "git_clone":
        return git_clone(**tool_input)
    if name == "run_semgrep":
        return run_semgrep(**tool_input)
    if name == "run_gitleaks":
        return run_gitleaks(**tool_input)
    if name == "read_file":
        return read_file(**tool_input)
    if name == "query_kb":
        return query_kb(**tool_input)
    if name == "write_artifact":
        return write_artifact(**tool_input)
    if name == "render_pdf":
        return render_pdf(**tool_input)
    raise ValueError(f"Unknown tool: {name}")


def _serialize_tool_result(result: Any) -> str:
    """Convert a tool result to a string, truncating if necessary."""
    if isinstance(result, str):
        text = result
    else:
        text = json.dumps(result, default=str)
    if len(text) > _TOOL_RESULT_MAX_CHARS:
        text = text[:_TOOL_RESULT_MAX_CHARS] + f"\n\n[... truncated at {_TOOL_RESULT_MAX_CHARS} chars ...]"
    return text


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """You are a senior SOC 2 compliance auditor with expertise in cloud-native software systems. You are analyzing a GitHub repository on behalf of a client who needs a SOC 2 Type II audit readiness assessment.

Your task is to systematically audit the repository and produce a structured compliance report covering the following SOC 2 Trust Service Criteria:

  CC6.1  - Logical access security (authentication, authorization)
  CC6.2  - User credential security (password hashing, MFA)
  CC6.3  - Least privilege and access removal
  CC6.6  - Application security (injection, XSS, CSRF)
  CC6.7  - Data transmission security (TLS enforcement)
  CC6.8  - Unauthorized/malicious software (dependency scanning)
  CC7.1  - Vulnerability monitoring (CVE detection in CI)
  CC7.2  - System monitoring (audit logging, anomaly detection)
  CC7.3  - Incident response readiness
  CC8.1  - Change management (PR workflow, branch protection)
  CC8.2  - Configuration management (IaC, secrets handling)
  CC9.1  - Risk identification
  CC9.2  - Third-party and supply chain risk

PROCESS:

Step 1 - Clone the repository.

Step 2 - Run automated scans:
  - run_semgrep to detect code security vulnerabilities
  - run_gitleaks to detect secrets and credentials in the codebase
  Read key files to understand the project structure:
  - Dependency files (package.json, requirements.txt, Pipfile, go.mod, pom.xml, Gemfile)
  - Infrastructure files (Dockerfile, docker-compose.yml, Kubernetes manifests)
  - CI/CD configuration (.github/workflows/, .gitlab-ci.yml, Jenkinsfile)
  - Security policy (SECURITY.md, .env.example)
  - README.md

Step 3 - Query the knowledge base for each control area to understand requirements.

Step 4 - For each SOC 2 control, assess status as one of:
  - pass:    Evidence in the repository demonstrates the control is implemented.
  - fail:    Evidence shows the control is not implemented or is broken.
  - partial: Some evidence present but gaps remain.
  - unknown: Insufficient evidence in the repository to make an assessment
             (the control may be implemented at the runtime/process level).

Step 5 - Write findings.json using write_artifact. Use this exact schema for each finding:
  {
    "control": "CC6.1",
    "title": "Logical Access Security",
    "status": "pass" | "fail" | "partial" | "unknown",
    "evidence": "Specific files/patterns found that support the assessment.",
    "gaps": "Specific missing controls or vulnerabilities found (empty string if none).",
    "recommendation": "Actionable remediation step (empty string if status is pass).",
    "severity": "critical" | "high" | "medium" | "low" | "info"
  }

Step 6 - Call render_pdf with the title, the findings list, and metadata including repo_url and scan_date.

GUIDELINES:
- Cite specific file paths and line numbers when possible.
- Severity for a 'fail': critical if it directly exposes credentials or bypasses auth; high for exploitable code vulnerabilities; medium for configuration gaps; low for documentation/process gaps.
- Do not speculate about controls you cannot observe in the repository. Use 'unknown' when in doubt.
- Do not include em dashes in your output. Use commas, semicolons, or colons instead.
- When a scan produces no findings, that is positive evidence for the relevant control.
- Complete all steps in order. The render_pdf call is your final action."""


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def _run_loop(
    client: anthropic.Anthropic,
    initial_message: str,
    budget: TokenBudget,
    transcript: TranscriptLogger,
) -> None:
    """Main agent loop. Runs until end_turn or budget exceeded."""

    messages: list[dict] = [{"role": "user", "content": initial_message}]
    transcript.log("user", "text", {"text": initial_message})

    while True:
        remaining_output = budget.remaining_output()
        max_tokens = min(_MAX_TOKENS_PER_TURN, remaining_output)

        logger.info("Calling Claude (%s), max_tokens=%d", _MODEL, max_tokens)

        response = client.messages.create(
            model=_MODEL,
            max_tokens=max_tokens,
            system=_SYSTEM_PROMPT,
            tools=TOOL_DEFINITIONS,
            messages=messages,
        )

        budget.record(response.usage.input_tokens, response.usage.output_tokens)

        # Log each content block in the response
        for block in response.content:
            if block.type == "text":
                transcript.log("assistant", "text", {"text": block.text})
                logger.info("Claude: %s", block.text[:200])
            elif block.type == "tool_use":
                transcript.log(
                    "assistant",
                    "tool_use",
                    {"tool_use_id": block.id, "name": block.name, "input": block.input},
                )
                logger.info("Tool call: %s(%s)", block.name, str(block.input)[:120])

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            logger.info("Agent finished (end_turn).")
            break

        if response.stop_reason == "max_tokens":
            logger.warning("Response hit max_tokens; continuing loop.")
            # The agent may not have called tools yet; add a nudge
            messages.append({
                "role": "user",
                "content": "Please continue.",
            })
            transcript.log("user", "text", {"text": "Please continue."})
            continue

        # Process tool calls
        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                start = time.monotonic()
                try:
                    raw_result = _dispatch_tool(block.name, block.input)
                    result_str = _serialize_tool_result(raw_result)
                    is_error = False
                    elapsed = time.monotonic() - start
                    logger.info(
                        "Tool %s completed in %.1fs, result length=%d",
                        block.name, elapsed, len(result_str),
                    )
                except Exception as exc:
                    result_str = f"Tool error: {exc}"
                    is_error = True
                    logger.error("Tool %s failed: %s", block.name, exc)

                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_str,
                    **({"is_error": True} if is_error else {}),
                })

                transcript.log(
                    "user",
                    "tool_result",
                    {
                        "tool_use_id": block.id,
                        "name": block.name,
                        "content": result_str[:2000],  # store preview in DB
                        "is_error": is_error,
                    },
                )

            messages.append({"role": "user", "content": tool_results})

        # Recheck budget after processing tool calls
        if budget.input_used >= budget.max_input or budget.output_used >= budget.max_output:
            raise TokenBudgetExceeded("Token budget exhausted after tool execution.")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(job_id: str, input_payload: dict) -> None:
    """Run the compliance report agent.

    Called from sandbox/run_agent.py with job_id and the job's input_payload.
    Exits normally on success; raises on failure (sandbox will exit non-zero).
    """
    repo_url = input_payload.get("repo_url", "").strip()
    if not repo_url:
        raise ValueError("compliance_report requires 'repo_url' in input_payload")

    logger.info("compliance_report agent starting: job=%s repo=%s", job_id, repo_url)

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    budget = TokenBudget(
        max_input=MAX_INPUT_TOKENS,
        max_output=MAX_OUTPUT_TOKENS,
    )

    transcript = TranscriptLogger(job_id=job_id)

    scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    initial_message = (
        f"Please perform a SOC 2 compliance audit of the following GitHub repository:\n\n"
        f"Repository URL: {repo_url}\n"
        f"Job ID: {job_id}\n"
        f"Scan date: {scan_date}\n\n"
        f"Follow the process described in your instructions. "
        f"Start by cloning the repository, then run the automated scans, "
        f"read key files, query the knowledge base, compile your findings, "
        f"and render the PDF report."
    )

    try:
        _run_loop(
            client=client,
            initial_message=initial_message,
            budget=budget,
            transcript=transcript,
        )
    except TokenBudgetExceeded as exc:
        logger.error("Token budget exceeded: %s", exc)
        # Write whatever we have so far
        transcript.log(
            "system",
            "text",
            {"text": f"TERMINATED: token budget exceeded. {exc}"},
        )
        raise
    except Exception as exc:
        logger.exception("Agent loop failed: %s", exc)
        transcript.log("system", "text", {"text": f"TERMINATED: {exc}"})
        raise
    finally:
        # Always flush the transcript, even on failure
        transcript.close()

    logger.info(
        "compliance_report agent done. Tokens: input=%d output=%d",
        budget.input_used,
        budget.output_used,
    )
