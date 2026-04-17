"""Six-step plan validation.

    1. Schema validation
    2. Operation whitelist check
    3. Parameter validation
    4. Risk classification
    5. Approval gating
    6. Execution (handed off to OpenCLAW; not performed here)

Returns a ValidationResult describing per-action outcomes plus a single
``ok`` flag that is True only if every step of every action passed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from validator.risk import RiskLevel, classify, needs_approval
from validator.schema import ACTION_PLAN_SCHEMA
from validator.whitelist import (
    ALLOWED_OPERATIONS,
    FORBIDDEN_PATTERNS,
    FORBIDDEN_READ_PATHS,
    WHITELISTED_SERVICES,
)


class ValidationCode(str, Enum):
    SCHEMA_INVALID = "SCHEMA_INVALID"
    OPERATION_NOT_ALLOWED = "OPERATION_NOT_ALLOWED"
    PARAMETER_INVALID = "PARAMETER_INVALID"
    SERVICE_NOT_WHITELISTED = "SERVICE_NOT_WHITELISTED"
    FORBIDDEN_PATTERN = "FORBIDDEN_PATTERN"
    FORBIDDEN_PATH = "FORBIDDEN_PATH"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"


@dataclass
class ValidationIssue:
    code: ValidationCode
    message: str
    action_id: str | None = None


@dataclass
class ActionDecision:
    action_id: str
    risk: RiskLevel
    approved: bool
    approval_required: bool
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues and (not self.approval_required or self.approved)


@dataclass
class ValidationResult:
    decisions: list[ActionDecision]
    issues: list[ValidationIssue]

    @property
    def ok(self) -> bool:
        return not self.issues and all(d.ok for d in self.decisions)


# ---------- step 1: schema validation ----------


def _validate_schema(plan: dict) -> list[ValidationIssue]:
    """Minimal dependency-free structural validation against ACTION_PLAN_SCHEMA.

    Full jsonschema is great, but we keep this dependency-free: we enforce
    the exact shape HomeOS expects. If a heavier validator is desired, swap
    in jsonschema.validate here.
    """
    out: list[ValidationIssue] = []
    if not isinstance(plan, dict):
        out.append(
            ValidationIssue(
                code=ValidationCode.SCHEMA_INVALID,
                message="plan must be an object",
            )
        )
        return out
    actions = plan.get("actions")
    if not isinstance(actions, list):
        out.append(
            ValidationIssue(
                code=ValidationCode.SCHEMA_INVALID,
                message="plan.actions must be an array",
            )
        )
        return out
    item_schema = ACTION_PLAN_SCHEMA["properties"]["actions"]["items"]
    required = set(item_schema["required"])
    allowed_targets = set(
        item_schema["properties"]["target"]["enum"]
    )
    allowed_operations = set(
        item_schema["properties"]["operation"]["enum"]
    )
    allowed_props = set(item_schema["properties"].keys())

    seen_ids: set[str] = set()
    for i, action in enumerate(actions):
        action_id = action.get("id") if isinstance(action, dict) else None
        if not isinstance(action, dict):
            out.append(
                ValidationIssue(
                    code=ValidationCode.SCHEMA_INVALID,
                    message=f"actions[{i}] must be an object",
                )
            )
            continue
        extra = set(action.keys()) - allowed_props
        if extra:
            out.append(
                ValidationIssue(
                    code=ValidationCode.SCHEMA_INVALID,
                    message=f"actions[{i}] has disallowed keys: {sorted(extra)}",
                    action_id=action_id,
                )
            )
        missing = required - action.keys()
        if missing:
            out.append(
                ValidationIssue(
                    code=ValidationCode.SCHEMA_INVALID,
                    message=f"actions[{i}] missing required keys: {sorted(missing)}",
                    action_id=action_id,
                )
            )
            continue
        if action["target"] not in allowed_targets:
            out.append(
                ValidationIssue(
                    code=ValidationCode.SCHEMA_INVALID,
                    message=f"actions[{i}].target invalid: {action['target']}",
                    action_id=action_id,
                )
            )
        if action["operation"] not in allowed_operations:
            out.append(
                ValidationIssue(
                    code=ValidationCode.SCHEMA_INVALID,
                    message=f"actions[{i}].operation invalid: {action['operation']}",
                    action_id=action_id,
                )
            )
        if not isinstance(action.get("parameters"), dict):
            out.append(
                ValidationIssue(
                    code=ValidationCode.SCHEMA_INVALID,
                    message=f"actions[{i}].parameters must be an object",
                    action_id=action_id,
                )
            )
        if not isinstance(action.get("expected_outcome"), str):
            out.append(
                ValidationIssue(
                    code=ValidationCode.SCHEMA_INVALID,
                    message=f"actions[{i}].expected_outcome must be a string",
                    action_id=action_id,
                )
            )
        if action_id in seen_ids:
            out.append(
                ValidationIssue(
                    code=ValidationCode.SCHEMA_INVALID,
                    message=f"duplicate action id: {action_id}",
                    action_id=action_id,
                )
            )
        if action_id:
            seen_ids.add(action_id)
    return out


# ---------- step 2: operation whitelist ----------


def _check_operation_whitelist(action: dict) -> list[ValidationIssue]:
    target = action.get("target")
    operation = action.get("operation")
    allowed = ALLOWED_OPERATIONS.get(target, frozenset())
    if operation not in allowed:
        return [
            ValidationIssue(
                code=ValidationCode.OPERATION_NOT_ALLOWED,
                message=f"operation {operation!r} not allowed on target {target!r}",
                action_id=action.get("id"),
            )
        ]
    return []


# ---------- step 3: parameter validation ----------


def _scan_forbidden(value: object) -> list[str]:
    """Return human-readable descriptions of any forbidden-pattern hits."""
    hits: list[str] = []
    if isinstance(value, str):
        for pattern in FORBIDDEN_PATTERNS:
            if pattern.search(value):
                hits.append(pattern.pattern)
    elif isinstance(value, dict):
        for v in value.values():
            hits.extend(_scan_forbidden(v))
    elif isinstance(value, list):
        for v in value:
            hits.extend(_scan_forbidden(v))
    return hits


def _check_parameters(action: dict) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    action_id = action.get("id")
    operation = action.get("operation")
    parameters = action.get("parameters", {})

    # Forbidden substrings anywhere in parameters.
    hits = _scan_forbidden(parameters)
    for pat in hits:
        issues.append(
            ValidationIssue(
                code=ValidationCode.FORBIDDEN_PATTERN,
                message=f"forbidden pattern matched: {pat}",
                action_id=action_id,
            )
        )

    # Operation-specific parameter checks.
    if operation == "call_service":
        for key in ("domain", "service"):
            if not isinstance(parameters.get(key), str) or not parameters[key]:
                issues.append(
                    ValidationIssue(
                        code=ValidationCode.PARAMETER_INVALID,
                        message=f"call_service requires string parameter {key!r}",
                        action_id=action_id,
                    )
                )
    elif operation == "get_state":
        if action.get("target") == "home_assistant":
            if not isinstance(parameters.get("entity_id"), str):
                issues.append(
                    ValidationIssue(
                        code=ValidationCode.PARAMETER_INVALID,
                        message="get_state requires string parameter 'entity_id'",
                        action_id=action_id,
                    )
                )
        elif action.get("target") == "api":
            if not isinstance(parameters.get("url"), str):
                issues.append(
                    ValidationIssue(
                        code=ValidationCode.PARAMETER_INVALID,
                        message="api get_state requires string parameter 'url'",
                        action_id=action_id,
                    )
                )
    elif operation in ("systemctl_status", "systemctl_restart"):
        service = parameters.get("service")
        if not isinstance(service, str) or not service:
            issues.append(
                ValidationIssue(
                    code=ValidationCode.PARAMETER_INVALID,
                    message=f"{operation} requires string parameter 'service'",
                    action_id=action_id,
                )
            )
        elif operation == "systemctl_restart" and service not in WHITELISTED_SERVICES:
            issues.append(
                ValidationIssue(
                    code=ValidationCode.SERVICE_NOT_WHITELISTED,
                    message=(
                        f"service {service!r} is not whitelisted for restart "
                        f"(allowed: {sorted(WHITELISTED_SERVICES)})"
                    ),
                    action_id=action_id,
                )
            )
    elif operation in ("docker_logs",):
        if not isinstance(parameters.get("container"), str):
            issues.append(
                ValidationIssue(
                    code=ValidationCode.PARAMETER_INVALID,
                    message="docker_logs requires string parameter 'container'",
                    action_id=action_id,
                )
            )
    elif operation == "docker_ps":
        pass  # no required parameters
    elif operation == "journalctl_tail":
        unit = parameters.get("unit")
        if not isinstance(unit, str) or not unit:
            issues.append(
                ValidationIssue(
                    code=ValidationCode.PARAMETER_INVALID,
                    message="journalctl_tail requires string parameter 'unit'",
                    action_id=action_id,
                )
            )
        lines = parameters.get("lines", 100)
        if not isinstance(lines, int) or lines <= 0 or lines > 10000:
            issues.append(
                ValidationIssue(
                    code=ValidationCode.PARAMETER_INVALID,
                    message="journalctl_tail 'lines' must be 1..10000",
                    action_id=action_id,
                )
            )
    elif operation == "read_file":
        path = parameters.get("path")
        if not isinstance(path, str) or not path:
            issues.append(
                ValidationIssue(
                    code=ValidationCode.PARAMETER_INVALID,
                    message="read_file requires string parameter 'path'",
                    action_id=action_id,
                )
            )
        else:
            for pat in FORBIDDEN_READ_PATHS:
                if pat.match(path):
                    issues.append(
                        ValidationIssue(
                            code=ValidationCode.FORBIDDEN_PATH,
                            message=f"read_file path is forbidden: {path}",
                            action_id=action_id,
                        )
                    )
                    break
    return issues


# ---------- orchestration ----------


def validate_plan(
    plan: dict,
    *,
    approvals: set[str] | None = None,
) -> ValidationResult:
    """Run all five validation steps. ``approvals`` is the set of action ids
    that have been pre-approved by a human (passed in from the API layer)."""
    approvals = approvals or set()

    schema_issues = _validate_schema(plan)
    if schema_issues:
        return ValidationResult(decisions=[], issues=schema_issues)

    decisions: list[ActionDecision] = []
    for action in plan["actions"]:
        issues: list[ValidationIssue] = []
        issues.extend(_check_operation_whitelist(action))
        issues.extend(_check_parameters(action))
        risk = classify(action)
        approval_required = needs_approval(action, risk)
        approved = action.get("id") in approvals
        if approval_required and not approved:
            issues.append(
                ValidationIssue(
                    code=ValidationCode.APPROVAL_REQUIRED,
                    message=f"action {action.get('id')!r} requires human approval",
                    action_id=action.get("id"),
                )
            )
        decisions.append(
            ActionDecision(
                action_id=action.get("id") or "",
                risk=risk,
                approved=approved,
                approval_required=approval_required,
                issues=issues,
            )
        )

    return ValidationResult(decisions=decisions, issues=[])
