"""
Quality Gate - Content quality checkpoint between extraction steps.

Runs AFTER an extractor succeeds but BEFORE returning the result.
If content fails, the extraction chain tries the next extractor.

Philosophy:
- Be strict about paywall/preview detection
- Give grace to archive sources (they're often the only option)
- Never keep partial content that has zero value
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, List, Set
from urllib.parse import urlparse

from argus.logging import get_logger

logger = get_logger("quality_gate")


class GateResult(Enum):
    """Quality gate decision."""
    PASS = "pass"
    FAIL = "fail"


@dataclass
class QualityGateEvaluation:
    """Result of quality gate evaluation."""
    decision: GateResult
    reason: str
    checks_passed: List[str] = field(default_factory=list)
    checks_failed: List[str] = field(default_factory=list)
    word_count: int = 0
    metadata: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.decision == GateResult.PASS


# Content type thresholds (minimum words)
THRESHOLDS = {
    "article": 100,
    "newsletter": 100,
    "transcript": 500,
    "video": 200,
    "email": 50,
    "note": 10,
}

# Preview/truncation patterns indicating partial/paywall content
PREVIEW_PATTERNS = [
    r"subscribe\s+to\s+(continue|read|access)",
    r"sign\s+in\s+to\s+(continue|read|access)",
    r"create\s+(a\s+)?free\s+account",
    r"this\s+(content|article)\s+is\s+(for\s+)?(subscribers|members)\s+only",
    r"to\s+read\s+the\s+full\s+(article|story)",
    r"premium\s+(content|article|subscriber)",
    r"unlock\s+this\s+(article|story|content)",
    r"become\s+a\s+(member|subscriber)",
    r"get\s+unlimited\s+access",
    r"already\s+a\s+subscriber\?\s*sign\s+in",
    r"register\s+to\s+continue\s+reading",
    r"start\s+your\s+subscription",
    r"this\s+is\s+a\s+preview",
    r"continue\s+reading\s+on",
    r"read\s+more\s+at",
    r"full\s+article\s+available\s+to\s+subscribers",
    r"limited\s+access",
    r"free\s+articles?\s+remaining",
    r"you('ve| have)\s+reached\s+your\s+(free\s+)?limit",
    r"only\s+available\s+to\s+(paid\s+)?(subscribers|members)",
]

# Hard paywall domains
HIGH_RISK_DOMAINS = {
    "nytimes.com", "newyorker.com", "wsj.com", "bloomberg.com",
    "ft.com", "washingtonpost.com", "theatlantic.com", "economist.com",
    "newyorkmag.com", "technologyreview.com", "theathletic.com",
}

# Archive sources that get lower thresholds
ARCHIVE_DOMAINS = {
    "archive.is", "archive.today", "web.archive.org", "archive.org",
    "archive.fo", "archive.ph",
}

_PREVIEW_REGEXES = [re.compile(p, re.IGNORECASE) for p in PREVIEW_PATTERNS]


class QualityGate:
    """Content quality gate checked between extraction steps."""

    def __init__(self):
        self.high_risk_domains = HIGH_RISK_DOMAINS.copy()
        self.archive_domains = ARCHIVE_DOMAINS.copy()

    def evaluate(
        self,
        content: str,
        source_url: str,
        content_type: str = "article",
        extractor: str = None,
    ) -> QualityGateEvaluation:
        """
        Evaluate content against quality gate.

        Args:
            content: Extracted text content
            source_url: Original URL
            content_type: article, transcript, video, etc.
            extractor: Which extractor produced this (wayback, archive_is, etc.)

        Returns:
            QualityGateEvaluation with decision and reason
        """
        checks_passed = []
        checks_failed = []
        metadata = {}

        # Notes always pass
        if content_type == "note" or '/note/' in source_url:
            return QualityGateEvaluation(
                decision=GateResult.PASS,
                reason="note_exempt",
                checks_passed=["note_exempt"],
                word_count=len(content.split()),
            )

        words = content.split()
        word_count = len(words)
        metadata['word_count'] = word_count

        min_words = THRESHOLDS.get(content_type, 100)

        parsed = urlparse(source_url)
        domain = parsed.netloc.replace('www.', '').lower()
        metadata['domain'] = domain

        is_archive = (
            any(arch in domain for arch in self.archive_domains)
            or extractor in ('wayback', 'archive_is')
        )
        metadata['is_archive'] = is_archive

        is_high_risk = any(hrd in domain for hrd in self.high_risk_domains)
        metadata['is_high_risk'] = is_high_risk

        # CHECK 1: Minimum word count
        if word_count >= min_words:
            checks_passed.append("min_words")
        else:
            if is_archive and word_count >= min_words // 2:
                checks_passed.append("min_words_grace")
            else:
                checks_failed.append("min_words")
                return QualityGateEvaluation(
                    decision=GateResult.FAIL,
                    reason=f"too_few_words: {word_count} < {min_words}",
                    checks_passed=checks_passed,
                    checks_failed=checks_failed,
                    word_count=word_count,
                    metadata=metadata,
                )

        # CHECK 2: Preview/truncation patterns
        check_region = content[:1000] + content[-1000:] if len(content) > 2000 else content
        check_region_lower = check_region.lower()

        preview_matches = []
        for regex in _PREVIEW_REGEXES:
            if regex.search(check_region_lower):
                preview_matches.append(regex.pattern[:30])

        if preview_matches:
            if len(preview_matches) >= 2:
                checks_failed.append("preview_patterns")
                metadata['preview_matches'] = preview_matches[:3]
                return QualityGateEvaluation(
                    decision=GateResult.FAIL,
                    reason=f"preview_patterns_detected: {preview_matches[:2]}",
                    checks_passed=checks_passed,
                    checks_failed=checks_failed,
                    word_count=word_count,
                    metadata=metadata,
                )
            elif is_high_risk and len(preview_matches) >= 1 and word_count < 500:
                checks_failed.append("preview_patterns")
                metadata['preview_matches'] = preview_matches[:3]
                return QualityGateEvaluation(
                    decision=GateResult.FAIL,
                    reason=f"high_risk_paywall: {domain} with preview pattern",
                    checks_passed=checks_passed,
                    checks_failed=checks_failed,
                    word_count=word_count,
                    metadata=metadata,
                )
            else:
                metadata['potential_preview'] = preview_matches[:3]
                checks_passed.append("no_preview")
        else:
            checks_passed.append("no_preview")

        # CHECK 3: High-risk domain with suspicious short content
        if is_high_risk and word_count < 500 and not is_archive:
            checks_failed.append("high_risk_short")
            return QualityGateEvaluation(
                decision=GateResult.FAIL,
                reason=f"high_risk_short_content: {domain} only {word_count} words",
                checks_passed=checks_passed,
                checks_failed=checks_failed,
                word_count=word_count,
                metadata=metadata,
            )

        return QualityGateEvaluation(
            decision=GateResult.PASS,
            reason="all_checks_passed",
            checks_passed=checks_passed,
            checks_failed=checks_failed,
            word_count=word_count,
            metadata=metadata,
        )

    def quick_check(self, content: str, content_type: str = "article") -> bool:
        """Fast pre-filter: word count + top preview patterns only."""
        word_count = len(content.split())
        min_words = THRESHOLDS.get(content_type, 100)
        if word_count < min_words:
            return False

        check_region = content[:500].lower()
        for regex in _PREVIEW_REGEXES[:5]:
            if regex.search(check_region):
                return False
        return True
