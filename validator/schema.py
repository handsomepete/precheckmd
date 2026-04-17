"""JSON Schema for Claude-generated action plans.

Mirrors the schema published with HomeOS. Every plan is an object with an
``actions`` array; each action targets one of {home_assistant, ssh, api} and
performs one of a fixed set of operations. The validator parses with this
schema as step 1.
"""

from __future__ import annotations

ACTION_PLAN_SCHEMA: dict = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["actions"],
    "properties": {
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "id",
                    "target",
                    "operation",
                    "parameters",
                    "expected_outcome",
                ],
                "properties": {
                    "id": {"type": "string", "minLength": 1},
                    "target": {
                        "type": "string",
                        "enum": ["home_assistant", "ssh", "api"],
                    },
                    "operation": {
                        "type": "string",
                        "enum": [
                            "call_service",
                            "get_state",
                            "systemctl_status",
                            "systemctl_restart",
                            "docker_ps",
                            "docker_logs",
                            "journalctl_tail",
                            "read_file",
                        ],
                    },
                    "parameters": {
                        "type": "object",
                        "additionalProperties": True,
                    },
                    "expected_outcome": {"type": "string"},
                    "risk_level": {
                        "type": "string",
                        "enum": ["low", "medium", "high"],
                        "default": "low",
                    },
                    "requires_approval": {
                        "type": "boolean",
                        "default": False,
                    },
                },
                "additionalProperties": False,
            },
        }
    },
    "additionalProperties": False,
}
