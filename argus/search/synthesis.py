"""
Multi-source synthesis for deep research queries.

Unlike simple Q&A, synthesis:
1. Retrieves from MULTIPLE sources (not just top-k from one)
2. Groups results by source/author
3. Runs a second LLM pass to compare/contrast/synthesize

Synthesis modes:
- compare: How do sources agree/disagree?
- timeline: How has thinking evolved over time?
- summarize: Key insights across all sources
- contradict: Find contradictions between sources
"""

import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
from collections import defaultdict
from datetime import datetime

import requests

from .config import get_config, AskConfig
from .retriever import HybridRetriever, RetrievalResult

logger = logging.getLogger(__name__)


@dataclass
class SourceCluster:
    """A group of chunks from the same source."""
    source_id: str
    source_name: str
    chunks: List[RetrievalResult]
    date: Optional[datetime] = None

    @property
    def combined_text(self) -> str:
        """Combine all chunk texts."""
        return "\n\n".join(c.text for c in self.chunks)

    @property
    def avg_score(self) -> float:
        """Average relevance score."""
        if not self.chunks:
            return 0.0
        return sum(c.score for c in self.chunks) / len(self.chunks)


@dataclass
class SynthesisResult:
    """Result of a synthesis query."""
    query: str
    mode: str
    synthesis: str
    clusters: List[SourceCluster]
    sources: List[str]
    model: str
    tokens_used: int
    confidence: str

    def __str__(self) -> str:
        return f"Synthesis ({self.mode}, {len(self.sources)} sources):\n{self.synthesis}"


# Synthesis prompt templates
SYNTHESIS_PROMPTS = {
    "compare": """Analyze how these sources agree and disagree on the topic.

Structure your response as:
1. **Points of Agreement**: Where do multiple sources align?
2. **Points of Disagreement**: Where do they differ?
3. **Nuances**: Any subtle differences in framing or emphasis?

Be specific - cite which sources say what.""",

    "timeline": """Analyze how thinking on this topic has evolved across these sources.

Structure your response as:
1. **Earlier perspectives**: What did older sources say?
2. **Evolution**: How has the thinking shifted?
3. **Current state**: What's the most recent perspective?

Note any pivotal moments or shifts in understanding.""",

    "summarize": """Synthesize the key insights from all sources on this topic.

Structure your response as:
1. **Core insights**: The most important takeaways
2. **Supporting evidence**: Key facts/arguments from sources
3. **Gaps**: What questions remain unanswered?

Integrate perspectives rather than listing them separately.""",

    "contradict": """Identify contradictions and tensions between these sources.

Structure your response as:
1. **Direct contradictions**: Where sources explicitly disagree
2. **Implicit tensions**: Where assumptions or framings conflict
3. **Resolution attempts**: Can these be reconciled?

Be precise about what each source claims.""",
}


class MultiSourceSynthesizer:
    """
    Synthesize insights from multiple sources.

    Unlike simple Q&A, this ensures diversity of sources and
    provides structured analysis across them.
    """

    SYSTEM_PROMPT = """You are a research analyst synthesizing information from multiple sources.

Your job is to:
1. Analyze content from different authors/sources
2. Compare, contrast, and integrate their perspectives
3. Cite sources specifically (use the source names provided)
4. Be objective - present what sources say, not your opinion
5. Note uncertainties and gaps in the evidence

Format responses in clear markdown with headers."""

    def __init__(self, config: Optional[AskConfig] = None):
        self.config = config or get_config()
        self.retriever = HybridRetriever(self.config)
        self.api_key = self.config.api_key
        self.base_url = self.config.base_url

        # Use configured model, or default to a stronger model for synthesis
        self.model = self.config.llm.model

        if not self.api_key:
            raise ValueError("OPENROUTER_API_KEY not set")

    def synthesize(
        self,
        query: str,
        mode: str = "summarize",
        min_sources: int = 3,
        max_chunks_per_source: int = 3,
        max_total_chunks: int = 15,
        source_filter: Optional[List[str]] = None,
    ) -> SynthesisResult:
        """
        Perform multi-source synthesis.

        Args:
            query: Research question
            mode: Synthesis mode (compare, timeline, summarize, contradict)
            min_sources: Minimum different sources to include
            max_chunks_per_source: Max chunks from any single source
            max_total_chunks: Total chunk limit
            source_filter: Optional list of source IDs to limit to

        Returns:
            SynthesisResult with synthesized analysis
        """
        if mode not in SYNTHESIS_PROMPTS:
            raise ValueError(f"Unknown mode: {mode}. Use: {list(SYNTHESIS_PROMPTS.keys())}")

        # Step 1: Retrieve with diversity
        clusters = self._retrieve_diverse(
            query,
            min_sources=min_sources,
            max_per_source=max_chunks_per_source,
            max_total=max_total_chunks,
            source_filter=source_filter,
        )

        if len(clusters) < min_sources:
            logger.warning(
                f"Only found {len(clusters)} sources, wanted {min_sources}"
            )

        if not clusters:
            return SynthesisResult(
                query=query,
                mode=mode,
                synthesis="Could not find enough diverse sources to synthesize.",
                clusters=[],
                sources=[],
                model=self.model,
                tokens_used=0,
                confidence="low",
            )

        # Step 2: Build context with source structure
        context = self._build_structured_context(clusters)

        # Step 3: Run synthesis LLM call
        synthesis, tokens_used = self._call_llm(query, context, mode)

        # Determine confidence
        confidence = self._estimate_confidence(clusters)

        return SynthesisResult(
            query=query,
            mode=mode,
            synthesis=synthesis,
            clusters=clusters,
            sources=[c.source_name for c in clusters],
            model=self.model,
            tokens_used=tokens_used,
            confidence=confidence,
        )

    def _retrieve_diverse(
        self,
        query: str,
        min_sources: int,
        max_per_source: int,
        max_total: int,
        source_filter: Optional[List[str]] = None,
    ) -> List[SourceCluster]:
        """
        Retrieve chunks ensuring source diversity.

        Strategy: Retrieve extra results, then prune to ensure
        we have at least min_sources represented.
        """
        # Retrieve more than we need to ensure diversity
        results = self.retriever.retrieve(
            query,
            limit=max_total * 3,  # Over-retrieve
            content_ids=source_filter,
        )

        # Group by source
        by_source: Dict[str, List[RetrievalResult]] = defaultdict(list)
        for r in results:
            source_id = r.content_id
            if len(by_source[source_id]) < max_per_source:
                by_source[source_id].append(r)

        # Build clusters
        clusters = []
        for source_id, chunks in by_source.items():
            # Get source name from metadata
            source_name = chunks[0].metadata.get("title", source_id) if chunks else source_id

            # Try to extract date from metadata
            date = None
            if chunks and "date" in chunks[0].metadata:
                try:
                    date = datetime.fromisoformat(chunks[0].metadata["date"])
                except (ValueError, TypeError):
                    pass

            clusters.append(SourceCluster(
                source_id=source_id,
                source_name=source_name,
                chunks=chunks,
                date=date,
            ))

        # Sort by average score
        clusters.sort(key=lambda c: c.avg_score, reverse=True)

        # Ensure we have enough sources (take top scoring up to max_total chunks)
        selected = []
        total_chunks = 0

        for cluster in clusters:
            if total_chunks >= max_total:
                break

            # Limit chunks from this source
            available = max_total - total_chunks
            cluster.chunks = cluster.chunks[:min(max_per_source, available)]
            total_chunks += len(cluster.chunks)
            selected.append(cluster)

            if len(selected) >= min_sources * 2:  # Stop if we have plenty
                break

        return selected

    def _build_structured_context(self, clusters: List[SourceCluster]) -> str:
        """Build context string organized by source."""
        parts = []

        for i, cluster in enumerate(clusters, 1):
            date_str = ""
            if cluster.date:
                date_str = f" ({cluster.date.strftime('%Y-%m-%d')})"

            header = f"## Source {i}: {cluster.source_name}{date_str}"
            body = cluster.combined_text

            parts.append(f"{header}\n\n{body}")

        return "\n\n---\n\n".join(parts)

    def _call_llm(self, query: str, context: str, mode: str) -> tuple[str, int]:
        """Call the LLM for synthesis."""
        mode_prompt = SYNTHESIS_PROMPTS[mode]

        user_prompt = f"""Research Question: {query}

{mode_prompt}

---

# Sources

{context}

---

Based on the sources above, provide your {mode} analysis:"""

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/atlas",
            "X-Title": "Atlas Synthesis",
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 2000,
            "temperature": 0.4,  # Slightly higher for synthesis
        }

        try:
            response = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=120,  # Longer timeout for synthesis
            )
            response.raise_for_status()
            data = response.json()

            synthesis = data["choices"][0]["message"]["content"]
            tokens_used = data.get("usage", {}).get("total_tokens", 0)

            logger.info(
                f"Synthesis complete: {len(clusters)} sources, {tokens_used} tokens"
            )

            return synthesis, tokens_used

        except requests.RequestException as e:
            logger.error(f"Synthesis LLM call failed: {e}")
            raise

    def _estimate_confidence(self, clusters: List[SourceCluster]) -> str:
        """Estimate confidence based on source diversity."""
        if len(clusters) >= 4:
            return "high"
        elif len(clusters) >= 2:
            return "medium"
        return "low"

    def close(self):
        """Close the retriever."""
        self.retriever.close()


# Convenience function
def synthesize_query(
    query: str,
    mode: str = "summarize",
    min_sources: int = 3,
) -> SynthesisResult:
    """
    Perform multi-source synthesis on a query.

    Args:
        query: Research question
        mode: compare, timeline, summarize, or contradict
        min_sources: Minimum sources to include

    Returns:
        SynthesisResult with analysis
    """
    synth = MultiSourceSynthesizer()
    try:
        return synth.synthesize(query, mode=mode, min_sources=min_sources)
    finally:
        synth.close()
