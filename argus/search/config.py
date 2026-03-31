"""
Configuration for Atlas Ask module.

Loads settings from config/ask_config.yml and environment variables.
All API calls go through OpenRouter for single billing.
"""

import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field

import yaml


@dataclass
class EmbeddingConfig:
    """Embedding model configuration."""
    model: str = "openai/text-embedding-3-small"
    dimensions: int = 1536
    batch_size: int = 100


@dataclass
class LLMConfig:
    """LLM configuration - ONE model for all tasks."""
    model: str = "google/gemini-2.5-flash-lite"
    summary_max_tokens: int = 150
    max_tags: int = 10
    max_context_tokens: int = 8000


@dataclass
class ChunkingConfig:
    """Content chunking configuration."""
    max_chunk_tokens: int = 512
    overlap_tokens: int = 50


@dataclass
class RetrievalConfig:
    """Retrieval/search configuration."""
    vector_weight: float = 0.7
    keyword_weight: float = 0.3
    max_results: int = 20


@dataclass
class AskConfig:
    """Complete Atlas Ask configuration."""
    provider: str = "openrouter"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key: Optional[str] = None

    embeddings: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    chunking: ChunkingConfig = field(default_factory=ChunkingConfig)
    retrieval: RetrievalConfig = field(default_factory=RetrievalConfig)

    # Paths
    vector_db_path: Path = field(default_factory=lambda: Path("data/indexes/atlas_vectors.db"))


_config: Optional[AskConfig] = None


def load_config(config_path: Optional[Path] = None) -> AskConfig:
    """Load configuration from YAML file and environment."""
    if config_path is None:
        config_path = Path(__file__).parent.parent.parent / "config" / "ask_config.yml"

    config = AskConfig()

    # Load from YAML if exists
    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}

        if "provider" in data:
            config.provider = data["provider"]
        if "base_url" in data:
            config.base_url = data["base_url"]

        if "embeddings" in data:
            emb = data["embeddings"]
            config.embeddings = EmbeddingConfig(
                model=emb.get("model", config.embeddings.model),
                dimensions=emb.get("dimensions", config.embeddings.dimensions),
                batch_size=emb.get("batch_size", config.embeddings.batch_size),
            )

        if "llm" in data:
            llm = data["llm"]
            config.llm = LLMConfig(
                model=llm.get("model", config.llm.model),
                summary_max_tokens=llm.get("summary_max_tokens", config.llm.summary_max_tokens),
                max_tags=llm.get("max_tags", config.llm.max_tags),
                max_context_tokens=llm.get("max_context_tokens", config.llm.max_context_tokens),
            )

        if "chunking" in data:
            chunk = data["chunking"]
            config.chunking = ChunkingConfig(
                max_chunk_tokens=chunk.get("max_chunk_tokens", config.chunking.max_chunk_tokens),
                overlap_tokens=chunk.get("overlap_tokens", config.chunking.overlap_tokens),
            )

        if "retrieval" in data:
            ret = data["retrieval"]
            config.retrieval = RetrievalConfig(
                vector_weight=ret.get("vector_weight", config.retrieval.vector_weight),
                keyword_weight=ret.get("keyword_weight", config.retrieval.keyword_weight),
                max_results=ret.get("max_results", config.retrieval.max_results),
            )

    # Override with environment variables
    config.api_key = os.getenv("OPENROUTER_API_KEY")

    return config


def get_config() -> AskConfig:
    """Get or create the global configuration."""
    global _config
    if _config is None:
        _config = load_config()
    return _config
