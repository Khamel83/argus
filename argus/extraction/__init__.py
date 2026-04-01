"""
Argus content extraction layer.

Given a URL from search results, fetch and extract clean text.
Closes the loop: search → identify useful link → extract → answer.

Usage:
    from argus.extraction import extract_url
    result = await extract_url("https://example.com/article")
    print(result.text)
"""

from argus.extraction.extractor import extract_url
from argus.extraction.models import ExtractedContent, ExtractorName

__all__ = ["extract_url", "ExtractedContent", "ExtractorName"]
