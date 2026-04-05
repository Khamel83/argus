"""
SSRF Protection - Block requests to internal/private networks.

Ported from Atlas modules/ingest/robust_fetcher.py.
Called before any extraction attempt to prevent Server-Side Request Forgery.
"""

import ipaddress
import socket
from urllib.parse import urlparse


def is_safe_url(url: str) -> tuple[bool, str]:
    """
    Validate URL is safe to fetch (SSRF prevention).

    Blocks:
    - Private IP ranges (10.x, 192.168.x, 172.16-31.x)
    - Localhost and loopback addresses
    - Link-local addresses (169.254.x.x)
    - Internal hostnames
    - Non-HTTP(S) schemes

    Returns:
        Tuple of (is_safe, reason)
    """
    try:
        parsed = urlparse(url)

        if parsed.scheme not in ('http', 'https'):
            return False, f"Invalid scheme: {parsed.scheme}"

        hostname = parsed.hostname
        if not hostname:
            return False, "No hostname in URL"

        # Block internal hostnames
        internal_patterns = [
            'localhost', 'internal', 'intranet', 'local',
            '.local', '.internal', '.corp', '.lan',
        ]
        hostname_lower = hostname.lower()
        for pattern in internal_patterns:
            if hostname_lower == pattern or hostname_lower.endswith(pattern):
                return False, f"Internal hostname blocked: {hostname}"

        # Resolve and check IP addresses
        try:
            resolved = socket.getaddrinfo(
                hostname, parsed.port or (443 if parsed.scheme == 'https' else 80)
            )
            for family, _, _, _, sockaddr in resolved:
                ip_str = sockaddr[0]
                try:
                    ip = ipaddress.ip_address(ip_str)
                    if ip.is_private:
                        return False, f"Private IP blocked: {ip_str}"
                    if ip.is_loopback:
                        return False, f"Loopback IP blocked: {ip_str}"
                    if ip.is_link_local:
                        return False, f"Link-local IP blocked: {ip_str}"
                    if ip.is_reserved:
                        return False, f"Reserved IP blocked: {ip_str}"
                    if ip.is_multicast:
                        return False, f"Multicast IP blocked: {ip_str}"
                except ValueError:
                    continue
        except socket.gaierror:
            # DNS resolution failed — let the request fail naturally
            pass

        return True, ""

    except Exception as e:
        return False, f"URL validation error: {e}"
