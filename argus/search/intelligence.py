"""
Atlas Intelligence Layer - Advanced search and analysis features.

Features:
- Quote extraction with attribution
- Source-specific queries ("What does X think about Y")
- Research threads (save related queries as projects)
- Smart recommendations based on annotations
- Contradiction detection across sources
- Conversational context for follow-up queries
"""

import logging
import sqlite3
import json
import hashlib
from typing import List, Optional, Dict, Any, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import get_config, AskConfig
from .retriever import HybridRetriever, RetrievalResult
from .annotations import AnnotationStore

logger = logging.getLogger(__name__)


# ============================================================================
# QUOTE EXTRACTION
# ============================================================================

@dataclass
class Quote:
    """An extracted quotable passage with attribution."""
    text: str
    source: str           # e.g., "Ben Thompson"
    source_title: str     # e.g., "Stratechery - AI and Jobs"
    date: Optional[str]
    chunk_id: str
    relevance_score: float

    def __str__(self) -> str:
        date_str = f" ({self.date})" if self.date else ""
        return f'"{self.text}"\n  — {self.source}, {self.source_title}{date_str}'

    def as_markdown(self) -> str:
        date_str = f" ({self.date})" if self.date else ""
        return f'> "{self.text}"\n>\n> — **{self.source}**, *{self.source_title}*{date_str}'


class QuoteExtractor:
    """Extract quotable passages from search results."""

    def __init__(self, config: Optional[AskConfig] = None):
        self.config = config or get_config()
        self.retriever = HybridRetriever(self.config)

    def extract_quotes(
        self,
        topic: str,
        limit: int = 5,
        min_length: int = 50,
        max_length: int = 300,
        source_filter: Optional[List[str]] = None,
    ) -> List[Quote]:
        """
        Extract quotable passages on a topic.

        Args:
            topic: The topic to find quotes about
            limit: Maximum number of quotes to return
            min_length: Minimum quote length in characters
            max_length: Maximum quote length in characters
            source_filter: Only include quotes from these sources
        """
        # Get more results than needed to filter
        results = self.retriever.retrieve(topic, limit=limit * 3)

        quotes = []
        for result in results:
            # Extract the most quotable sentence(s)
            quote_text = self._extract_best_quote(
                result.text,
                min_length=min_length,
                max_length=max_length
            )

            if not quote_text:
                continue

            # Get source info from metadata
            source = self._infer_source(result.metadata)
            source_title = result.metadata.get("title", result.content_id)
            date = result.metadata.get("date") or result.metadata.get("published_date")

            # Apply source filter
            if source_filter:
                if not any(sf.lower() in source.lower() for sf in source_filter):
                    continue

            quotes.append(Quote(
                text=quote_text,
                source=source,
                source_title=source_title,
                date=date,
                chunk_id=result.chunk_id,
                relevance_score=result.score,
            ))

            if len(quotes) >= limit:
                break

        return quotes

    def _extract_best_quote(self, text: str, min_length: int, max_length: int) -> Optional[str]:
        """Extract the most quotable passage from text."""
        import re

        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', text)

        # Score sentences by "quotability"
        scored = []
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < min_length or len(sent) > max_length:
                continue

            score = self._quote_score(sent)
            scored.append((score, sent))

        if not scored:
            # Try combining sentences
            for i in range(len(sentences) - 1):
                combined = sentences[i].strip() + " " + sentences[i+1].strip()
                if min_length <= len(combined) <= max_length:
                    score = self._quote_score(combined)
                    scored.append((score, combined))

        if not scored:
            return None

        # Return highest scored
        scored.sort(reverse=True)
        return scored[0][1]

    def _quote_score(self, text: str) -> float:
        """Score how quotable a piece of text is."""
        score = 0.0
        text_lower = text.lower()

        # Prefer declarative statements
        if not text.endswith("?"):
            score += 1.0

        # Prefer insight words
        insight_words = ["because", "therefore", "means", "shows", "proves",
                        "important", "key", "crucial", "fundamental", "essentially",
                        "always", "never", "everyone", "no one"]
        for word in insight_words:
            if word in text_lower:
                score += 0.5

        # Penalize filler words
        filler_words = ["um", "uh", "like", "you know", "i mean", "sort of", "kind of"]
        for word in filler_words:
            if word in text_lower:
                score -= 0.3

        # Prefer medium length (not too short, not too long)
        ideal_length = 150
        length_penalty = abs(len(text) - ideal_length) / 100
        score -= length_penalty * 0.2

        return score

    def _infer_source(self, metadata: Dict) -> str:
        """Infer the source/author from metadata."""
        # Try explicit author field
        if metadata.get("author"):
            return metadata["author"]

        # Try to infer from content_id or title
        content_id = metadata.get("content_id", "")

        # Map common podcast/content IDs to authors
        source_map = {
            "stratechery": "Ben Thompson",
            "acquired": "Ben Gilbert & David Rosenthal",
            "ezra-klein": "Ezra Klein",
            "hard-fork": "Kevin Roose & Casey Newton",
            "tyler": "Tyler Cowen",
            "dwarkesh": "Dwarkesh Patel",
            "lex-fridman": "Lex Fridman",
            "all-in": "All-In Podcast",
            "dithering": "Ben Thompson & John Gruber",
        }

        for key, author in source_map.items():
            if key in content_id.lower():
                return author

        return metadata.get("sitename", "Unknown Source")

    def close(self):
        self.retriever.close()


# ============================================================================
# SOURCE-SPECIFIC QUERIES
# ============================================================================

@dataclass
class SourcePerspective:
    """A source's perspective on a topic."""
    source: str
    source_id: str
    summary: str
    key_points: List[str]
    quotes: List[Quote]
    chunk_count: int
    avg_relevance: float


class SourceQueryEngine:
    """Query what specific sources think about topics."""

    def __init__(self, config: Optional[AskConfig] = None):
        self.config = config or get_config()
        self.retriever = HybridRetriever(self.config)
        self.quote_extractor = QuoteExtractor(self.config)

    def what_does_x_think(
        self,
        source: str,
        topic: str,
        limit: int = 5,
    ) -> Optional[SourcePerspective]:
        """
        Get a specific source's perspective on a topic.

        Args:
            source: Source name (e.g., "Ben Thompson", "Ezra Klein")
            topic: The topic to query about
            limit: Max chunks to consider
        """
        # Build query that emphasizes the source
        query = f"{topic} {source}"

        results = self.retriever.retrieve(query, limit=limit * 2)

        # Filter to only this source
        source_lower = source.lower()
        filtered = []
        for r in results:
            r_source = self._infer_source(r.metadata).lower()
            content_id = r.content_id.lower()

            if source_lower in r_source or source_lower in content_id:
                filtered.append(r)

        if not filtered:
            return None

        filtered = filtered[:limit]

        # Get quotes from this source
        quotes = self.quote_extractor.extract_quotes(
            topic,
            limit=3,
            source_filter=[source]
        )

        # Synthesize perspective using LLM
        summary, key_points = self._synthesize_perspective(source, topic, filtered)

        return SourcePerspective(
            source=source,
            source_id=filtered[0].content_id,
            summary=summary,
            key_points=key_points,
            quotes=quotes,
            chunk_count=len(filtered),
            avg_relevance=sum(r.score for r in filtered) / len(filtered),
        )

    def compare_sources(
        self,
        sources: List[str],
        topic: str,
    ) -> Dict[str, SourcePerspective]:
        """Compare what multiple sources think about a topic."""
        perspectives = {}
        for source in sources:
            perspective = self.what_does_x_think(source, topic)
            if perspective:
                perspectives[source] = perspective
        return perspectives

    def _synthesize_perspective(
        self,
        source: str,
        topic: str,
        results: List[RetrievalResult]
    ) -> Tuple[str, List[str]]:
        """Use LLM to synthesize the source's perspective."""
        import requests

        if not self.config.api_key:
            # Fallback without LLM
            combined = " ".join(r.text[:500] for r in results[:3])
            return combined[:500], []

        context = "\n\n---\n\n".join(
            f"From: {r.metadata.get('title', r.content_id)}\n{r.text}"
            for r in results
        )

        prompt = f"""Based on the following excerpts from {source}, summarize their perspective on "{topic}".

Excerpts:
{context}

Provide:
1. A 2-3 sentence summary of {source}'s perspective
2. 3-5 key points as bullet points

Format as:
SUMMARY: [summary here]

KEY POINTS:
- [point 1]
- [point 2]
- [point 3]"""

        try:
            response = requests.post(
                f"{self.config.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.llm.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 500,
                    "temperature": 0.3,
                },
                timeout=30,
            )
            response.raise_for_status()

            content = response.json()["choices"][0]["message"]["content"]

            # Parse response
            summary = ""
            key_points = []

            if "SUMMARY:" in content:
                summary = content.split("SUMMARY:")[1].split("KEY POINTS:")[0].strip()

            if "KEY POINTS:" in content:
                points_text = content.split("KEY POINTS:")[1]
                for line in points_text.split("\n"):
                    line = line.strip()
                    if line.startswith("-") or line.startswith("•"):
                        key_points.append(line[1:].strip())

            return summary, key_points

        except Exception as e:
            logger.error(f"LLM synthesis failed: {e}")
            return results[0].text[:500] if results else "", []

    def _infer_source(self, metadata: Dict) -> str:
        """Infer source from metadata."""
        return self.quote_extractor._infer_source(metadata)

    def close(self):
        self.retriever.close()
        self.quote_extractor.close()


# ============================================================================
# RESEARCH THREADS
# ============================================================================

@dataclass
class ThreadQuery:
    """A single query within a research thread."""
    id: str
    query: str
    answer: str
    sources: List[str]
    created_at: datetime


@dataclass
class ResearchThread:
    """A collection of related queries forming a research session."""
    id: str
    title: str
    description: Optional[str]
    queries: List[ThreadQuery]
    created_at: datetime
    updated_at: datetime
    tags: List[str] = field(default_factory=list)


class ThreadStore:
    """Persistent storage for research threads."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or Path("data/indexes/research_threads.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS threads (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    description TEXT,
                    tags TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS thread_queries (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    sources TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (thread_id) REFERENCES threads(id)
                );

                CREATE INDEX IF NOT EXISTS idx_thread_queries_thread
                ON thread_queries(thread_id);
            """)

    def create_thread(
        self,
        title: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> ResearchThread:
        """Create a new research thread."""
        thread_id = hashlib.md5(f"{title}{datetime.now().isoformat()}".encode()).hexdigest()[:12]
        now = datetime.now()

        thread = ResearchThread(
            id=thread_id,
            title=title,
            description=description,
            queries=[],
            created_at=now,
            updated_at=now,
            tags=tags or [],
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO threads (id, title, description, tags, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (thread.id, thread.title, thread.description, json.dumps(thread.tags),
                 thread.created_at.isoformat(), thread.updated_at.isoformat())
            )

        return thread

    def add_query(
        self,
        thread_id: str,
        query: str,
        answer: str,
        sources: List[str],
    ) -> ThreadQuery:
        """Add a query to an existing thread."""
        query_id = hashlib.md5(f"{thread_id}{query}{datetime.now().isoformat()}".encode()).hexdigest()[:12]
        now = datetime.now()

        thread_query = ThreadQuery(
            id=query_id,
            query=query,
            answer=answer,
            sources=sources,
            created_at=now,
        )

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO thread_queries (id, thread_id, query, answer, sources, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (thread_query.id, thread_id, query, answer, json.dumps(sources), now.isoformat())
            )
            conn.execute(
                "UPDATE threads SET updated_at = ? WHERE id = ?",
                (now.isoformat(), thread_id)
            )

        return thread_query

    def get_thread(self, thread_id: str) -> Optional[ResearchThread]:
        """Get a thread by ID."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, title, description, tags, created_at, updated_at FROM threads WHERE id = ?",
                (thread_id,)
            ).fetchone()

            if not row:
                return None

            queries = []
            for q_row in conn.execute(
                "SELECT id, query, answer, sources, created_at FROM thread_queries WHERE thread_id = ? ORDER BY created_at",
                (thread_id,)
            ).fetchall():
                queries.append(ThreadQuery(
                    id=q_row[0],
                    query=q_row[1],
                    answer=q_row[2],
                    sources=json.loads(q_row[3]) if q_row[3] else [],
                    created_at=datetime.fromisoformat(q_row[4]),
                ))

            return ResearchThread(
                id=row[0],
                title=row[1],
                description=row[2],
                tags=json.loads(row[3]) if row[3] else [],
                created_at=datetime.fromisoformat(row[4]),
                updated_at=datetime.fromisoformat(row[5]),
                queries=queries,
            )

    def list_threads(self, limit: int = 20) -> List[ResearchThread]:
        """List recent threads."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id FROM threads ORDER BY updated_at DESC LIMIT ?",
                (limit,)
            ).fetchall()

            return [self.get_thread(row[0]) for row in rows if row]

    def search_threads(self, query: str) -> List[ResearchThread]:
        """Search threads by title or query content."""
        with sqlite3.connect(self.db_path) as conn:
            # Search in titles
            title_matches = conn.execute(
                "SELECT id FROM threads WHERE title LIKE ? LIMIT 10",
                (f"%{query}%",)
            ).fetchall()

            # Search in queries
            query_matches = conn.execute(
                "SELECT DISTINCT thread_id FROM thread_queries WHERE query LIKE ? LIMIT 10",
                (f"%{query}%",)
            ).fetchall()

            thread_ids = set(r[0] for r in title_matches) | set(r[0] for r in query_matches)
            return [self.get_thread(tid) for tid in thread_ids if tid]


# ============================================================================
# SMART RECOMMENDATIONS
# ============================================================================

class RecommendationEngine:
    """Generate recommendations based on user interests and annotations."""

    def __init__(self, config: Optional[AskConfig] = None):
        self.config = config or get_config()
        self.retriever = HybridRetriever(self.config)
        self.annotation_store = AnnotationStore()

    def get_recommendations(
        self,
        limit: int = 5,
        based_on: str = "annotations",
    ) -> List[RetrievalResult]:
        """
        Get personalized content recommendations.

        Args:
            limit: Number of recommendations
            based_on: What to base recommendations on
                     ("annotations", "recent", "interests")
        """
        # Get user's annotated content
        important_chunks = self.annotation_store.get_important_chunks()

        if not important_chunks:
            logger.info("No annotations found, falling back to recent content")
            return self._recommend_recent(limit)

        # Build query from annotated content topics
        topics = self._extract_topics(important_chunks)

        if not topics:
            return self._recommend_recent(limit)

        # Search for related but unseen content
        query = " ".join(topics[:5])
        results = self.retriever.retrieve(query, limit=limit * 3)

        # Filter out already-seen content
        seen_content_ids = {c["content_id"] for c in important_chunks}
        recommendations = [
            r for r in results
            if r.content_id not in seen_content_ids
        ][:limit]

        return recommendations

    def _extract_topics(self, chunks: List[Dict]) -> List[str]:
        """Extract topic keywords from chunks."""
        from collections import Counter
        import re

        # Simple keyword extraction
        all_text = " ".join(c.get("text", "")[:500] for c in chunks)

        # Remove common words
        stop_words = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                     "being", "have", "has", "had", "do", "does", "did", "will",
                     "would", "could", "should", "may", "might", "must", "shall",
                     "to", "of", "in", "for", "on", "with", "at", "by", "from",
                     "this", "that", "these", "those", "it", "its", "they", "them",
                     "and", "or", "but", "if", "then", "so", "as", "i", "you", "we"}

        words = re.findall(r'\b[a-zA-Z]{4,}\b', all_text.lower())
        words = [w for w in words if w not in stop_words]

        # Get most common
        counter = Counter(words)
        return [word for word, _ in counter.most_common(10)]

    def _recommend_recent(self, limit: int) -> List[RetrievalResult]:
        """Fallback: recommend recent high-quality content."""
        # This would need date-based retrieval
        # For now, just do a general quality query
        return self.retriever.retrieve("important insights analysis", limit=limit)

    def close(self):
        self.retriever.close()


# ============================================================================
# CONTRADICTION DETECTION
# ============================================================================

@dataclass
class Contradiction:
    """A detected contradiction between sources."""
    topic: str
    position_a: str
    source_a: str
    position_b: str
    source_b: str
    explanation: str
    confidence: float
    chunk_ids: List[str]


class ContradictionRadar:
    """Proactively detect contradictions across sources."""

    def __init__(self, config: Optional[AskConfig] = None):
        self.config = config or get_config()
        self.retriever = HybridRetriever(self.config)

    def find_contradictions(
        self,
        topic: str,
        min_sources: int = 2,
    ) -> List[Contradiction]:
        """
        Find contradictions on a topic across sources.
        """
        import requests

        # Get diverse results
        results = self.retriever.retrieve(topic, limit=20)

        if len(results) < min_sources:
            return []

        # Group by source
        by_source = {}
        for r in results:
            source = r.metadata.get("author") or r.content_id.split("_")[0]
            if source not in by_source:
                by_source[source] = []
            by_source[source].append(r)

        if len(by_source) < min_sources:
            return []

        # Use LLM to detect contradictions
        if not self.config.api_key:
            return []

        context = []
        for source, chunks in list(by_source.items())[:5]:
            text = chunks[0].text[:500]
            context.append(f"SOURCE: {source}\n{text}")

        prompt = f"""Analyze these excerpts about "{topic}" and identify any contradictions or disagreements between sources.

{chr(10).join(context)}

For each contradiction found, format as:
CONTRADICTION:
- Topic: [specific sub-topic]
- Source A: [source name] says [position]
- Source B: [source name] says [position]
- Explanation: [why these contradict]
- Confidence: [high/medium/low]

If no contradictions exist, respond with: NO CONTRADICTIONS FOUND"""

        try:
            response = requests.post(
                f"{self.config.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.config.llm.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1000,
                    "temperature": 0.3,
                },
                timeout=60,
            )
            response.raise_for_status()

            content = response.json()["choices"][0]["message"]["content"]

            if "NO CONTRADICTIONS" in content.upper():
                return []

            # Parse contradictions
            return self._parse_contradictions(content, topic, results)

        except Exception as e:
            logger.error(f"Contradiction detection failed: {e}")
            return []

    def _parse_contradictions(
        self,
        content: str,
        topic: str,
        results: List[RetrievalResult]
    ) -> List[Contradiction]:
        """Parse LLM response into Contradiction objects."""
        contradictions = []

        for block in content.split("CONTRADICTION:")[1:]:
            try:
                lines = block.strip().split("\n")

                sub_topic = ""
                source_a = ""
                position_a = ""
                source_b = ""
                position_b = ""
                explanation = ""
                confidence = 0.5

                for line in lines:
                    line = line.strip()
                    if line.startswith("- Topic:"):
                        sub_topic = line.replace("- Topic:", "").strip()
                    elif line.startswith("- Source A:"):
                        parts = line.replace("- Source A:", "").strip()
                        if " says " in parts:
                            source_a, position_a = parts.split(" says ", 1)
                    elif line.startswith("- Source B:"):
                        parts = line.replace("- Source B:", "").strip()
                        if " says " in parts:
                            source_b, position_b = parts.split(" says ", 1)
                    elif line.startswith("- Explanation:"):
                        explanation = line.replace("- Explanation:", "").strip()
                    elif line.startswith("- Confidence:"):
                        conf_str = line.replace("- Confidence:", "").strip().lower()
                        confidence = {"high": 0.9, "medium": 0.6, "low": 0.3}.get(conf_str, 0.5)

                if source_a and source_b:
                    contradictions.append(Contradiction(
                        topic=sub_topic or topic,
                        position_a=position_a,
                        source_a=source_a.strip(),
                        position_b=position_b,
                        source_b=source_b.strip(),
                        explanation=explanation,
                        confidence=confidence,
                        chunk_ids=[r.chunk_id for r in results[:4]],
                    ))
            except Exception as e:
                logger.debug(f"Failed to parse contradiction block: {e}")
                continue

        return contradictions

    def close(self):
        self.retriever.close()


# ============================================================================
# CONVERSATIONAL CONTEXT
# ============================================================================

@dataclass
class ConversationTurn:
    """A single turn in a conversation."""
    role: str  # "user" or "assistant"
    content: str
    sources: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)


class ConversationalSession:
    """Maintains context for follow-up queries."""

    def __init__(self, config: Optional[AskConfig] = None):
        self.config = config or get_config()
        self.retriever = HybridRetriever(self.config)
        self.history: List[ConversationTurn] = []
        self.context_chunks: List[RetrievalResult] = []

    def ask(self, query: str, include_history: bool = True) -> str:
        """
        Ask a question with conversational context.

        Args:
            query: The question to ask
            include_history: Whether to include previous turns
        """
        import requests

        # Get relevant chunks
        results = self.retriever.retrieve(query, limit=5)
        self.context_chunks = results

        # Build context from results
        context = "\n\n".join(
            f"From {r.metadata.get('title', r.content_id)}:\n{r.text}"
            for r in results
        )

        # Build messages
        messages = []

        # System message
        messages.append({
            "role": "system",
            "content": """You are a helpful research assistant with access to a personal knowledge base.
Answer questions based on the provided context. If the context doesn't contain relevant information, say so.
Be concise but thorough. Cite sources when possible."""
        })

        # Include history if requested
        if include_history and self.history:
            for turn in self.history[-6:]:  # Last 3 exchanges
                messages.append({
                    "role": turn.role,
                    "content": turn.content
                })

        # Current query with context
        messages.append({
            "role": "user",
            "content": f"""Context from knowledge base:
{context}

Question: {query}"""
        })

        # Add user turn to history
        self.history.append(ConversationTurn(role="user", content=query))

        if not self.config.api_key:
            answer = f"Based on {len(results)} sources: {results[0].text[:500]}..." if results else "No relevant content found."
        else:
            try:
                response = requests.post(
                    f"{self.config.base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self.config.llm.model,
                        "messages": messages,
                        "max_tokens": 1000,
                        "temperature": 0.3,
                    },
                    timeout=60,
                )
                response.raise_for_status()
                answer = response.json()["choices"][0]["message"]["content"]
            except Exception as e:
                logger.error(f"Conversational query failed: {e}")
                answer = f"Error: {e}"

        # Add assistant turn to history
        self.history.append(ConversationTurn(
            role="assistant",
            content=answer,
            sources=[r.content_id for r in results],
        ))

        return answer

    def followup(self, query: str) -> str:
        """Convenience method for follow-up questions."""
        return self.ask(query, include_history=True)

    def reset(self):
        """Clear conversation history."""
        self.history = []
        self.context_chunks = []

    def get_history(self) -> List[ConversationTurn]:
        """Get conversation history."""
        return self.history

    def close(self):
        self.retriever.close()
