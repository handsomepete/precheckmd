"""Tests for the Validator (schema, whitelist, risk, approvals)."""

from __future__ import annotations

from validator.risk import RiskLevel, classify, needs_approval
from validator.validator import ValidationCode, validate_plan


def _action(**overrides) -> dict:
    action = {
        "id": "a1",
        "target": "ssh",
        "operation": "systemctl_status",
        "parameters": {"service": "home-assistant"},
        "expected_outcome": "status ok",
    }
    action.update(overrides)
    return action


def _plan(*actions) -> dict:
    return {"actions": list(actions)}


# ---------- schema validation ----------


def test_plan_must_be_dict_with_actions_array():
    result = validate_plan({})
    assert not result.ok
    assert any(i.code == ValidationCode.SCHEMA_INVALID for i in result.issues)


def test_action_missing_required_keys_flagged():
    result = validate_plan({"actions": [{"id": "x"}]})
    assert not result.ok
    assert any(i.code == ValidationCode.SCHEMA_INVALID for i in result.issues)


def test_unknown_target_flagged():
    result = validate_plan(_plan(_action(target="smart_tv")))
    assert not result.ok
    codes = {i.code for i in result.issues}
    assert ValidationCode.SCHEMA_INVALID in codes


def test_duplicate_action_ids_flagged():
    result = validate_plan(_plan(_action(id="dup"), _action(id="dup")))
    assert not result.ok
    msgs = [i.message for i in result.issues]
    assert any("duplicate action id" in m for m in msgs)


def test_unknown_top_level_key_flagged():
    # disallowed key on action
    bad = _action(foo="bar")
    result = validate_plan(_plan(bad))
    assert not result.ok
    assert any(i.code == ValidationCode.SCHEMA_INVALID for i in result.issues)


# ---------- operation whitelist ----------


def test_operation_not_allowed_on_target():
    # call_service is valid on home_assistant, not ssh
    result = validate_plan(
        _plan(
            _action(
                target="ssh",
                operation="call_service",
                parameters={"domain": "light", "service": "turn_on"},
            )
        )
    )
    assert not result.ok
    # schema enum is OK (call_service is a valid op value), but whitelist rejects ssh+call_service
    assert any(
        i.code == ValidationCode.OPERATION_NOT_ALLOWED for d in result.decisions for i in d.issues
    )


def test_valid_ssh_status_passes_schema_and_whitelist():
    result = validate_plan(_plan(_action()))
    # systemctl_status is low risk and needs no approval
    assert result.ok, [i.message for d in result.decisions for i in d.issues] + [
        i.message for i in result.issues
    ]


# ---------- parameter validation ----------


def test_call_service_requires_domain_and_service():
    result = validate_plan(
        _plan(
            _action(
                id="hi",
                target="home_assistant",
                operation="call_service",
                parameters={"domain": "light"},  # missing service
                requires_approval=True,
            )
        )
    )
    assert not result.ok
    issues = [i for d in result.decisions for i in d.issues]
    assert any(i.code == ValidationCode.PARAMETER_INVALID for i in issues)


def test_systemctl_restart_service_must_be_whitelisted():
    action = _action(
        operation="systemctl_restart",
        parameters={"service": "postgresql"},
        requires_approval=True,
    )
    result = validate_plan(_plan(action), approvals={"a1"})
    assert not result.ok
    codes = {i.code for d in result.decisions for i in d.issues}
    assert ValidationCode.SERVICE_NOT_WHITELISTED in codes


def test_systemctl_restart_whitelisted_service_ok_with_approval():
    action = _action(
        operation="systemctl_restart",
        parameters={"service": "home-assistant"},
        requires_approval=True,
    )
    result = validate_plan(_plan(action), approvals={"a1"})
    assert result.ok, [i.message for d in result.decisions for i in d.issues]


def test_forbidden_pattern_in_parameters():
    action = _action(
        operation="read_file",
        parameters={"path": "/tmp/x", "cmd": "rm -rf /"},
    )
    result = validate_plan(_plan(action))
    assert not result.ok
    codes = {i.code for d in result.decisions for i in d.issues}
    assert ValidationCode.FORBIDDEN_PATTERN in codes


def test_read_file_forbidden_path():
    action = _action(
        operation="read_file",
        parameters={"path": "/etc/shadow"},
    )
    result = validate_plan(_plan(action))
    assert not result.ok
    codes = {i.code for d in result.decisions for i in d.issues}
    assert ValidationCode.FORBIDDEN_PATH in codes


def test_read_file_ssh_path_ok():
    action = _action(
        operation="read_file",
        parameters={"path": "/var/log/syslog"},
    )
    result = validate_plan(_plan(action))
    assert result.ok, [i.message for d in result.decisions for i in d.issues]


def test_journalctl_tail_line_bounds():
    action = _action(
        operation="journalctl_tail",
        parameters={"unit": "home-assistant", "lines": 0},
    )
    result = validate_plan(_plan(action))
    assert not result.ok
    assert any(
        i.code == ValidationCode.PARAMETER_INVALID
        for d in result.decisions
        for i in d.issues
    )


# ---------- risk classification ----------


def test_low_risk_read_operation():
    assert classify(_action(operation="get_state", target="home_assistant",
                            parameters={"entity_id": "light.kitchen"})) == RiskLevel.LOW


def test_medium_risk_for_restart():
    assert classify(_action(operation="systemctl_restart",
                            parameters={"service": "docker"})) == RiskLevel.MEDIUM


def test_declared_high_escalates():
    assert classify(_action(operation="get_state", target="home_assistant",
                            parameters={"entity_id": "x"},
                            risk_level="high")) == RiskLevel.HIGH


def test_approval_required_for_medium():
    action = _action(operation="systemctl_restart",
                     parameters={"service": "docker"})
    risk = classify(action)
    assert needs_approval(action, risk) is True


def test_approval_not_required_for_low_unless_declared():
    action = _action()
    risk = classify(action)
    assert needs_approval(action, risk) is False
    action["requires_approval"] = True
    assert needs_approval(action, risk) is True


# ---------- approval gating at plan level ----------


def test_medium_risk_action_without_approval_fails():
    action = _action(
        operation="systemctl_restart",
        parameters={"service": "docker"},
    )
    result = validate_plan(_plan(action))
    assert not result.ok
    codes = {i.code for d in result.decisions for i in d.issues}
    assert ValidationCode.APPROVAL_REQUIRED in codes


def test_medium_risk_action_with_approval_passes():
    action = _action(
        operation="systemctl_restart",
        parameters={"service": "docker"},
    )
    result = validate_plan(_plan(action), approvals={"a1"})
    assert result.ok, [i.message for d in result.decisions for i in d.issues]
