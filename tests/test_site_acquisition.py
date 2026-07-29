"""Regression coverage for same-site URL admission."""

import pytest

from argus.operations.site_acquisition import _same_site


@pytest.mark.parametrize(
    ("url", "expected"),
    (
        ("https://attacker.co.uk/path", False),
        ("https://victim.co.uk/path", True),
        ("https://docs.victim.co.uk/path", True),
        ("https://victim.co.uk:8443/path", True),
        ("https://evilvictim.co.uk/path", False),
        ("https://[not-a-valid-host/path", False),
    ),
)
def test_same_site_accepts_only_root_hostname_and_its_subdomains(url, expected):
    assert _same_site(url, "victim.co.uk") is expected
