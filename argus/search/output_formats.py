"""
Output format generators for synthesis results.

Converts SynthesisResult into various formats:
- briefing: Executive summary document
- email: Draft email referencing the research
- markdown: Standard markdown export
"""

import logging
from typing import Optional
from dataclasses import dataclass
from datetime import datetime

import requests

from .config import get_config, AskConfig
from .synthesis import SynthesisResult

logger = logging.getLogger(__name__)


@dataclass
class FormattedOutput:
    """A formatted output document."""
    format: str
    content: str
    title: str
    generated_at: datetime
    tokens_used: int = 0


def format_as_briefing(
    result: SynthesisResult,
    audience: str = "general",
    max_words: int = 500,
    config: Optional[AskConfig] = None,
) -> FormattedOutput:
    """
    Format synthesis result as an executive briefing.

    Args:
        result: The synthesis result to format
        audience: Target audience (general, technical, executive)
        max_words: Maximum word count
        config: Optional config override

    Returns:
        FormattedOutput with briefing document
    """
    config = config or get_config()

    if not config.api_key:
        # Fallback to simple formatting
        return _simple_briefing(result, audience)

    audience_contexts = {
        "general": "a general audience with basic technical understanding",
        "technical": "a technical audience familiar with the domain",
        "executive": "busy executives who need key insights quickly, skip technical details",
    }

    audience_context = audience_contexts.get(audience, audience_contexts["general"])

    prompt = f"""Transform this research synthesis into a professional briefing document for {audience_context}.

Original synthesis:
{result.synthesis}

Sources used: {', '.join(result.sources[:5])}

Requirements:
- Maximum {max_words} words
- Start with a clear summary (2-3 sentences)
- Use bullet points for key insights
- End with implications or recommendations
- Keep language clear and actionable
- Do not use jargon unless writing for technical audience

Write the briefing:"""

    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/atlas",
        "X-Title": "Atlas Output Format",
    }

    payload = {
        "model": config.llm.model,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 1000,
        "temperature": 0.3,
    }

    try:
        response = requests.post(
            f"{config.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()

        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", 0)

        return FormattedOutput(
            format="briefing",
            content=content,
            title=f"Briefing: {result.query}",
            generated_at=datetime.now(),
            tokens_used=tokens,
        )

    except requests.RequestException as e:
        logger.error(f"Briefing generation failed: {e}")
        return _simple_briefing(result, audience)


def _simple_briefing(result: SynthesisResult, audience: str) -> FormattedOutput:
    """Fallback simple briefing without LLM."""
    lines = [
        f"# Briefing: {result.query}",
        f"",
        f"**Audience**: {audience}",
        f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"",
        f"## Summary",
        f"",
        result.synthesis,
        f"",
        f"## Sources",
        f"",
    ]

    for source in result.sources:
        lines.append(f"- {source}")

    return FormattedOutput(
        format="briefing",
        content="\n".join(lines),
        title=f"Briefing: {result.query}",
        generated_at=datetime.now(),
    )


def format_as_email(
    result: SynthesisResult,
    recipient_context: str = "",
    config: Optional[AskConfig] = None,
) -> FormattedOutput:
    """
    Format synthesis result as an email draft.

    Args:
        result: The synthesis result to format
        recipient_context: Context about the recipient (e.g., "my manager", "a colleague")
        config: Optional config override

    Returns:
        FormattedOutput with email draft
    """
    config = config or get_config()

    if not config.api_key:
        return _simple_email(result, recipient_context)

    recipient_desc = recipient_context if recipient_context else "a professional colleague"

    prompt = f"""Write a professional email sharing research findings with {recipient_desc}.

Research topic: {result.query}

Key findings:
{result.synthesis}

Sources: {', '.join(result.sources[:3])}

Requirements:
- Professional but conversational tone
- Include subject line (start with "Subject: ")
- Brief intro explaining why you're sharing this
- 2-3 key takeaways in bullet form
- Optional: suggest a follow-up discussion
- Keep it concise (under 200 words for body)

Write the email:"""

    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/atlas",
        "X-Title": "Atlas Output Format",
    }

    payload = {
        "model": config.llm.model,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 500,
        "temperature": 0.4,
    }

    try:
        response = requests.post(
            f"{config.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()

        content = data["choices"][0]["message"]["content"]
        tokens = data.get("usage", {}).get("total_tokens", 0)

        return FormattedOutput(
            format="email",
            content=content,
            title=f"Email: {result.query}",
            generated_at=datetime.now(),
            tokens_used=tokens,
        )

    except requests.RequestException as e:
        logger.error(f"Email generation failed: {e}")
        return _simple_email(result, recipient_context)


def _simple_email(result: SynthesisResult, recipient_context: str) -> FormattedOutput:
    """Fallback simple email without LLM."""
    recipient = recipient_context if recipient_context else "recipient"

    lines = [
        f"Subject: Research findings on {result.query}",
        f"",
        f"Hi,",
        f"",
        f"I wanted to share some research I did on '{result.query}'.",
        f"",
        f"Key findings:",
        f"",
        result.synthesis[:500],
        f"",
        f"Sources: {', '.join(result.sources[:3])}",
        f"",
        f"Let me know if you'd like to discuss further.",
        f"",
        f"Best,",
    ]

    return FormattedOutput(
        format="email",
        content="\n".join(lines),
        title=f"Email: {result.query}",
        generated_at=datetime.now(),
    )


def format_as_markdown(result: SynthesisResult) -> FormattedOutput:
    """
    Format synthesis result as clean markdown.

    Args:
        result: The synthesis result to format

    Returns:
        FormattedOutput with markdown document
    """
    lines = [
        f"# {result.query}",
        f"",
        f"**Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Mode**: {result.mode}",
        f"**Confidence**: {result.confidence}",
        f"",
        f"---",
        f"",
        f"## Analysis",
        f"",
        result.synthesis,
        f"",
        f"---",
        f"",
        f"## Sources ({len(result.sources)})",
        f"",
    ]

    for i, source in enumerate(result.sources, 1):
        lines.append(f"{i}. {source}")

    lines.extend([
        f"",
        f"---",
        f"",
        f"*Generated with Atlas Ask ({result.tokens_used} tokens)*",
    ])

    return FormattedOutput(
        format="markdown",
        content="\n".join(lines),
        title=result.query,
        generated_at=datetime.now(),
    )


def save_output(output: FormattedOutput, output_dir: str = "data/outputs") -> str:
    """Save formatted output to file."""
    from pathlib import Path
    import re

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Clean title for filename
    safe_title = re.sub(r'[^\w\s-]', '', output.title)[:50].strip()
    safe_title = re.sub(r'[-\s]+', '-', safe_title)

    date_str = output.generated_at.strftime('%Y-%m-%d')
    filename = f"{date_str}_{safe_title}.md"

    filepath = output_path / filename
    filepath.write_text(output.content)

    logger.info(f"Saved output to {filepath}")
    return str(filepath)
