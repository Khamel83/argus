"""Tests for quality gate, soft 404, and SSRF protection."""

import pytest

from argus.extraction.quality_gate import QualityGate, GateResult
from argus.extraction.soft_404 import is_soft_404, soft_404_check
from argus.extraction.ssrf import is_safe_url


# --- Quality Gate ---

class TestQualityGate:
    def setup_method(self):
        self.gate = QualityGate()

    def test_q1_short_content_fails(self):
        """Q1: Short content (<100 words) should FAIL."""
        text = " ".join(["word"] * 50)
        result = self.gate.evaluate(text, "https://example.com/article")
        assert result.decision == GateResult.FAIL
        assert "too_few_words" in result.reason

    def test_q2_preview_patterns_detected(self):
        """Q2: Two+ preview patterns should FAIL."""
        text = (
            "subscribe to continue reading this article "
            + "sign in to access the full content "
            + " ".join(["word"] * 200)
        )
        result = self.gate.evaluate(text, "https://example.com/article")
        assert result.decision == GateResult.FAIL
        assert "preview" in result.reason

    def test_q3_single_pattern_passes(self):
        """Q3: Single false positive pattern should PASS."""
        text = (
            "subscribe to our newsletter for updates "
            + " ".join(["word"] * 200)
        )
        result = self.gate.evaluate(text, "https://example.com/blog/article")
        assert result.decision == GateResult.PASS

    def test_q4_high_risk_short_fails(self):
        """Q4: High-risk domain + short content should FAIL."""
        text = " ".join(["word"] * 200)
        result = self.gate.evaluate(text, "https://www.nytimes.com/article")
        assert result.decision == GateResult.FAIL
        assert "high_risk_short" in result.reason

    def test_q5_high_risk_long_passes(self):
        """Q5: High-risk domain + long content should PASS."""
        text = " ".join(["word"] * 2000)
        result = self.gate.evaluate(text, "https://www.nytimes.com/article")
        assert result.decision == GateResult.PASS

    def test_q6_archive_grace(self):
        """Q6: Archive source with 60 words should PASS (grace)."""
        text = " ".join(["word"] * 60)
        result = self.gate.evaluate(
            text, "https://web.archive.org/web/2020/https://example.com/article",
            extractor="wayback",
        )
        assert result.decision == GateResult.PASS

    def test_q7_transcript_threshold(self):
        """Q7: 300-word transcript should FAIL (need 500)."""
        text = " ".join(["word"] * 300)
        result = self.gate.evaluate(text, "https://example.com/podcast", content_type="transcript")
        assert result.decision == GateResult.FAIL

    def test_q8_soft_404_rejected(self):
        """Q8: Soft 404 content rejected by quality gate via short check."""
        text = "Sorry, we couldn't find that page. It may have been moved or deleted."
        result = self.gate.evaluate(text, "https://example.com/missing")
        assert result.decision == GateResult.FAIL

    def test_q9_note_exempt(self):
        """Q9: Notes always pass."""
        text = " ".join(["word"] * 5)
        result = self.gate.evaluate(text, "https://example.com/note/abc", content_type="note")
        assert result.decision == GateResult.PASS
        assert result.reason == "note_exempt"

    def test_q10_normal_article_passes(self):
        """Q10: Normal 500-word article should PASS."""
        text = " ".join(["word"] * 500)
        result = self.gate.evaluate(text, "https://example.com/article")
        assert result.decision == GateResult.PASS

    def test_quick_check_fast_reject(self):
        """quick_check should reject short content immediately."""
        text = " ".join(["word"] * 20)
        assert self.gate.quick_check(text) is False

    def test_quick_check_fast_accept(self):
        """quick_check should accept good content."""
        text = " ".join(["word"] * 500)
        assert self.gate.quick_check(text) is True


# --- Soft 404 ---

class TestSoft404:
    def test_real_soft_404(self):
        """Detects 'page not found' pattern."""
        text = "Page not found. Sorry, we couldn't find that page."
        assert is_soft_404(text) is True

    def test_short_text_is_soft_404(self):
        """Very short text is treated as soft 404."""
        assert is_soft_404("hello") is True
        assert is_soft_404("") is True

    def test_good_content_not_soft_404(self):
        """Normal article content is not a soft 404."""
        text = " ".join(["word"] * 200) + " This is a real article about technology."
        assert is_soft_404(text) is False

    def test_soft_404_check_tuple(self):
        """soft_404_check returns (bool, reason) tuple."""
        is_404, reason = soft_404_check("Page not found sorry")
        assert is_404 is True
        assert "soft_404" in reason

    def test_expired_content_detected(self):
        """Detects 'no longer available' pattern."""
        text = "This content is no longer available. " + " ".join(["word"] * 50)
        assert is_soft_404(text) is True


# --- SSRF ---

class TestSSRF:
    def test_s1_private_ip(self):
        """S1: Private IP blocked."""
        safe, reason = is_safe_url("http://192.168.1.1/admin")
        assert safe is False
        assert "Private" in reason

    def test_s2_loopback(self):
        """S2: Loopback blocked."""
        safe, reason = is_safe_url("http://localhost:8005/api/health")
        assert safe is False
        assert "Internal" in reason or "Loopback" in reason

    def test_s3_internal_hostname(self):
        """S3: Internal hostname blocked."""
        safe, reason = is_safe_url("http://internal.corp/db")
        assert safe is False
        assert "Internal" in reason

    def test_s4_link_local(self):
        """S4: Link-local (169.254.x.x) blocked."""
        safe, reason = is_safe_url("http://169.254.169.254/metadata")
        assert safe is False

    def test_s5_valid_https(self):
        """S5: Valid HTTPS passes."""
        safe, reason = is_safe_url("https://example.com/article")
        assert safe is True
        assert reason == ""

    def test_s6_valid_http(self):
        """S6: Valid HTTP passes."""
        safe, reason = is_safe_url("http://example.com/article")
        assert safe is True

    def test_non_http_blocked(self):
        """Non-HTTP(S) schemes blocked."""
        safe, _ = is_safe_url("ftp://example.com/file")
        assert safe is False

    def test_no_hostname_blocked(self):
        """URL without hostname blocked."""
        safe, _ = is_safe_url("not-a-url")
        assert safe is False
