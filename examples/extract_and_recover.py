"""Extract clean text from a URL, falling back to recovery if the URL is dead.

Run with:
    uv run python examples/extract_and_recover.py
"""

import asyncio

from argus.broker.router import create_broker
from argus.extraction import extract_url
from argus.models import SearchMode, SearchQuery


TARGET_URL = "https://example.com/"


async def main() -> None:
    print(f"Extracting: {TARGET_URL}")
    content = await extract_url(TARGET_URL)

    if content and content.text:
        print(f"  title:      {content.title}")
        print(f"  word_count: {content.word_count}")
        print(f"  source:     {content.source_type}")
        print(f"  preview:    {content.text[:200].strip()}...")
        return

    print("Extraction failed — falling back to recover-url search.\n")
    broker = create_broker()
    response = await broker.search(
        SearchQuery(
            query=TARGET_URL,
            mode=SearchMode.RECOVERY,
            max_results=3,
        )
    )
    for result in response.results:
        print(f"  candidate: {result.url}  ({result.provider})")


if __name__ == "__main__":
    asyncio.run(main())
