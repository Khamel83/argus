"""Build a local docs-plus-research pack programmatically.

This mirrors what `argus build-research-pack -t "..."` does from the CLI.

Run with:
    uv run python examples/research_pack.py

Output: a pack directory under your Argus runtime corpus root (run
`argus paths` to see exactly where).
"""

import asyncio

from argus.broker.router import create_broker
from argus.workflows import WorkflowService


async def main() -> None:
    broker = create_broker()
    service = WorkflowService(broker)

    result = await service.build_research_pack(
        topic="example sdk",
        official_url=None,  # e.g. "https://docs.example.com" to seed with official docs
        max_research_pages=8,
    )

    print(f"run_id:   {result.run_id}")
    print(f"status:   {result.status.value}")
    print(f"snapshot: {result.snapshot_dir}")
    if result.error:
        print(f"error:    {result.error}")
    if result.artifacts:
        print(f"artifacts ({len(result.artifacts)}):")
        for artifact in result.artifacts[:5]:
            print(f"  - {artifact.path}")


if __name__ == "__main__":
    asyncio.run(main())
