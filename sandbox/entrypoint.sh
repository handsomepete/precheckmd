#!/usr/bin/env bash
# Apply network egress restrictions, then exec the given command.
# Requires the container to be started with --cap-add NET_ADMIN.
#
# Allowed egress:
#   - loopback
#   - Docker internal networks (10/8, 172.16/12, 192.168/16) for postgres/redis
#   - DNS (UDP/TCP 53) so domain resolution works
#   - github.com (resolved at startup)
#   - api.anthropic.com (resolved at startup)
# Everything else is dropped.

set -euo pipefail

apply_iptables() {
    echo "[sandbox] Applying egress firewall rules..."

    # Allow loopback
    iptables -A OUTPUT -o lo -j ACCEPT

    # Allow already-established connections (responses)
    iptables -A OUTPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

    # Allow DNS so we can resolve allowed hosts
    iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
    iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT

    # Allow all RFC-1918 ranges (Docker bridge, compose networks, postgres)
    iptables -A OUTPUT -d 10.0.0.0/8     -j ACCEPT
    iptables -A OUTPUT -d 172.16.0.0/12  -j ACCEPT
    iptables -A OUTPUT -d 192.168.0.0/16 -j ACCEPT

    # Resolve and allow github.com
    for ip in $(getent ahostsv4 github.com 2>/dev/null | awk '{print $1}' | sort -u); do
        iptables -A OUTPUT -d "${ip}" -j ACCEPT
        echo "[sandbox]   allowed github.com -> ${ip}"
    done

    # Resolve and allow api.anthropic.com
    for ip in $(getent ahostsv4 api.anthropic.com 2>/dev/null | awk '{print $1}' | sort -u); do
        iptables -A OUTPUT -d "${ip}" -j ACCEPT
        echo "[sandbox]   allowed api.anthropic.com -> ${ip}"
    done

    # Drop all other OUTPUT
    iptables -P OUTPUT DROP
    echo "[sandbox] Egress firewall active."
}

# Only apply if running as root and NET_ADMIN is available
if [ "$(id -u)" = "0" ] && iptables -L OUTPUT -n > /dev/null 2>&1; then
    apply_iptables
else
    echo "[sandbox] WARNING: skipping iptables (not root or no NET_ADMIN). Egress unrestricted."
fi

exec "$@"
