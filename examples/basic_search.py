"""Minimal Argus search example — no API keys required.

Falls back to DuckDuckGo when no other providers are configured.

Run with:
    uv run python examples/basic_search.py
"""

import asyncio

from argus.broker.router import create_broker
from argus.models import SearchMode, SearchQuery


async def main() -> None:
    broker = create_broker()

    response = await broker.search(
        SearchQuery(
            query="python web frameworks",
            mode=SearchMode.DISCOVERY,
            max_results=5,
        )
    )

    print(f"Got {len(response.results)} results in mode={response.mode.value}\n")
    for i, result in enumerate(response.results, 1):
        print(f"{i}. {result.title}")
        print(f"   {result.url}")
        provider = result.provider.value if hasattr(result.provider, "value") else result.provider
        print(f"   provider={provider}  score={result.score:.4f}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
