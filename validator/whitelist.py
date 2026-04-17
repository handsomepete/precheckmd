"""Operation, service, and forbidden-pattern whitelists for the Validator."""

from __future__ import annotations

import re

# Allowed (target, operation) pairs.
ALLOWED_OPERATIONS: dict[str, frozenset[str]] = {
    "home_assistant": frozenset({"call_service", "get_state"}),
    "ssh": frozenset(
        {
            "systemctl_status",
            "systemctl_restart",
            "docker_ps",
            "docker_logs",
            "journalctl_tail",
            "read_file",
        }
    ),
    "api": frozenset({"get_state"}),  # generic GET-style API probes only
}

# Services that systemctl_restart is permitted to touch.
WHITELISTED_SERVICES: frozenset[str] = frozenset(
    {"home-assistant", "docker", "mosquitto"}
)

# Patterns that are never allowed in any parameter value, anywhere in the plan.
# These cover destructive shell operations, credential changes, network
# reconfiguration, and package installs (per the Validator spec).
FORBIDDEN_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\brm\s+-rf\b",
        r"\bmkfs\b",
        r"\bdd\s+if=",
        r":\(\)\{.*:\|:.*\};:",  # classic fork bomb
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bpoweroff\b",
        r"\bhalt\b",
        r"\bpasswd\b",
        r"\buseradd\b",
        r"\buserdel\b",
        r"\bchpasswd\b",
        r"\bvisudo\b",
        r"\bssh-keygen\b",
        r"\bauthorized_keys\b",
        r"\biptables\b",
        r"\bnft\b",
        r"\bufw\b",
        r"\bip\s+route\b",
        r"\bip\s+addr\b",
        r"\bifconfig\b",
        r"\bnmcli\b",
        r"\bapt(-get)?\s+(install|remove|purge|upgrade|dist-upgrade)\b",
        r"\byum\s+(install|remove|update)\b",
        r"\bdnf\s+(install|remove|update)\b",
        r"\bpip\s+install\b",
        r"\bnpm\s+install\b",
        r"\bcurl\b.*\|\s*sh\b",
        r"\bwget\b.*\|\s*sh\b",
        r"while\s+true",  # uncontrolled loops
        r"for\s*\(\s*;\s*;\s*\)",
    )
)

# File paths that read_file may not access.
FORBIDDEN_READ_PATHS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"^/etc/shadow$",
        r"^/etc/gshadow$",
        r"^/etc/sudoers(\.d/.*)?$",
        r"^.*\.ssh/.*",
        r"^.*\.aws/credentials$",
        r"^.*\.git-credentials$",
    )
)
