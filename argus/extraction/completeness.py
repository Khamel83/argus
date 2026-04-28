"""
Content Completeness Assessment

Determines whether extracted text is a full article or a truncated/partial version.
Used both inline during extraction (to decide whether to keep trying) and as a
standalone endpoint so callers can assess content they already have.

Signals checked:
  trailing_ellipsis     — text ends with ... or …
  feed_marker           — "read more", "continue reading", WordPress RSS footer, etc.
  mid_sentence_end      — last char is not sentence-terminal punctuation
  abrupt_final_para     — last paragraph much shorter than median, no terminal punctuation
  word_cap              — word count suspiciously near common RSS truncation caps

Confidence thresholds:
  >= 0.85  strong truncation (two+ signals or one hard signal) — extractor chain continues
  0.50-0.84 probable truncation — flagged but caller decides
  < 0.50   likely complete
"""

import re
from dataclasses import dataclass, field
from typing import List

# Patterns checked against the tail of the article (last 600 chars + first 100)
_FEED_MARKER_PATTERNS = [
    r'\[\s*\.\.\.\s*\]',                               # [...]
    r'read\s+more[\s:→]',
    r'continue\s+reading',
    r'full\s+(?:story|article|post)\s+(?:at|available)',
    r'\[read\s+more\]',
    r'click\s+here\s+to\s+read',
    r'view\s+(?:full|complete)\s+(?:post|article)',
    r'the\s+post\s+.{1,120}\s+appeared\s+first\s+on',  # WordPress RSS footer
    r'this\s+(?:article|post)\s+(?:was\s+)?originally\s+appeared\s+on',
    r'→\s*$',                                           # trailing arrow
    r'subscribe\s+(?:for|to)\s+more',
    r'sign\s+up\s+to\s+continue',
]
_FEED_MARKER_RE = [re.compile(p, re.IGNORECASE) for p in _FEED_MARKER_PATTERNS]

# Characters that legitimately end a sentence/article
_SENTENCE_TERMINALS = frozenset('.!?"\'»)]”’')

# Common RSS/feed word-count caps (± 5 words tolerance)
_WORD_COUNT_CAPS = [100, 150, 200, 250, 300, 350, 400, 450, 500, 600, 750]
_WORD_CAP_TOLERANCE = 6


@dataclass
class CompletenessResult:
    """Assessment of whether extracted content is a complete article."""
    is_complete: bool
    confidence: float            # 0.0–1.0 (how confident we are it's truncated when is_complete=False)
    truncation_type: str         # "clean" | "ellipsis" | "feed_marker" | "mid_sentence" | "abrupt_end" | "word_cap"
    signals: List[str] = field(default_factory=list)
    word_count: int = 0
    recommended_action: str = "use_as_is"  # "use_as_is" | "try_full_fetch"


def assess_completeness(text: str, url: str = "") -> CompletenessResult:
    """
    Assess whether `text` looks like a complete article.

    Returns a CompletenessResult. Call this after extraction succeeds and the
    quality gate passes — it runs fast (pure text heuristics, no I/O).
    """
    if not text or not text.strip():
        return CompletenessResult(
            is_complete=False,
            confidence=1.0,
            truncation_type="empty",
            signals=["empty_content"],
            word_count=0,
            recommended_action="try_full_fetch",
        )

    stripped = text.rstrip()
    word_count = len(stripped.split())
    signals: List[str] = []

    # --- Signal 1: Trailing ellipsis ---
    if stripped.endswith("...") or stripped.endswith("…"):
        signals.append("trailing_ellipsis")

    # --- Signal 2: Feed truncation markers ---
    # Check tail (last 600 chars) where these typically appear
    tail = stripped[-600:].lower() if len(stripped) > 600 else stripped.lower()
    for regex in _FEED_MARKER_RE:
        if regex.search(tail):
            signals.append("feed_marker")
            break

    # --- Signal 3: Mid-sentence end ---
    # Last non-whitespace character isn't sentence-terminal
    last_char = stripped[-1] if stripped else ""
    if last_char and last_char not in _SENTENCE_TERMINALS:
        if last_char.isalpha() or last_char.isdigit() or last_char in ",;:":
            signals.append("mid_sentence_end")

    # --- Signal 4: Abrupt final paragraph ---
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text) if p.strip()]
    if len(paragraphs) >= 3:
        last_para = paragraphs[-1]
        last_words = len(last_para.split())
        para_lengths = sorted(len(p.split()) for p in paragraphs)
        median_words = para_lengths[len(para_lengths) // 2]
        last_para_char = last_para.rstrip()[-1] if last_para.rstrip() else ""
        if (
            last_words < 8
            and median_words > 25
            and last_para_char not in _SENTENCE_TERMINALS
        ):
            signals.append("abrupt_final_para")

    # --- Signal 5: Suspicious word-count cap ---
    for cap in _WORD_COUNT_CAPS:
        if abs(word_count - cap) <= _WORD_CAP_TOLERANCE:
            signals.append(f"word_cap:{cap}")
            break

    # --- Compute confidence and primary truncation type ---
    strong = {"trailing_ellipsis", "feed_marker", "mid_sentence_end"}
    weak = {"abrupt_final_para"} | {s for s in signals if s.startswith("word_cap")}

    strong_hits = [s for s in signals if s in strong]
    weak_hits = [s for s in signals if s in weak or s.startswith("word_cap")]

    if len(strong_hits) >= 2:
        confidence = 0.95
        is_complete = False
    elif len(strong_hits) == 1 and len(weak_hits) >= 1:
        confidence = 0.90
        is_complete = False
    elif len(strong_hits) == 1:
        confidence = 0.85
        is_complete = False
    elif len(weak_hits) >= 2:
        confidence = 0.65
        is_complete = False
    elif len(weak_hits) == 1:
        confidence = 0.40
        is_complete = True   # weak signal alone — assume complete
    else:
        confidence = 0.05
        is_complete = True

    # Primary truncation type for human readability
    truncation_type = _primary_type(signals) if signals else "clean"
    recommended_action = "try_full_fetch" if not is_complete and confidence >= 0.65 else "use_as_is"

    return CompletenessResult(
        is_complete=is_complete,
        confidence=confidence,
        truncation_type=truncation_type,
        signals=signals,
        word_count=word_count,
        recommended_action=recommended_action,
    )


def _primary_type(signals: List[str]) -> str:
    """Return the most descriptive truncation type from the signal list."""
    priority = ["feed_marker", "trailing_ellipsis", "mid_sentence_end", "abrupt_final_para"]
    for p in priority:
        if p in signals:
            return p
    for s in signals:
        if s.startswith("word_cap"):
            return "word_cap"
    return "clean"
