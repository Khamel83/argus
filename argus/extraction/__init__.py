"""
Argus content extraction layer.

Integrated fallback chain with quality gates:
  trafilatura → playwright → jina → wayback → archive.is

Usage:
    from argus.extraction import extract_url
    result = await extract_url("https://example.com/article")
    print(result.text)
"""

from argus.extraction.extractor import extract_url
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.extraction.quality_gate import QualityGate, GateResult

__all__ = [
    "extract_url",
    "ExtractedContent",
    "ExtractorName",
    "QualityGate",
    "GateResult",
]
