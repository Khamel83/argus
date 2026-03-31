"""
Topic Map - Visualize what content you've consumed.

Generates an interactive HTML visualization showing:
- Topic clusters
- Content distribution by source
- Temporal patterns
- Connections between topics
"""

import logging
import sqlite3
import json
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter, defaultdict

from .config import get_config, AskConfig

logger = logging.getLogger(__name__)


@dataclass
class TopicCluster:
    """A cluster of related content."""
    id: str
    label: str
    keywords: List[str]
    content_count: int
    sources: List[str]
    avg_date: Optional[datetime]


@dataclass
class TopicMap:
    """A visualization of content topics."""
    clusters: List[TopicCluster]
    connections: List[Dict[str, Any]]  # Links between clusters
    source_distribution: Dict[str, int]
    date_distribution: Dict[str, int]
    total_content: int
    generated_at: datetime


class TopicMapper:
    """Generate topic maps from indexed content."""

    def __init__(self, config: Optional[AskConfig] = None):
        self.config = config or get_config()
        self.db_path = self.config.vector_db_path

    def generate_map(
        self,
        limit: int = 1000,
        min_cluster_size: int = 5,
    ) -> TopicMap:
        """
        Generate a topic map from indexed content.

        Args:
            limit: Max chunks to analyze
            min_cluster_size: Minimum items per cluster
        """
        # Get content metadata
        content_data = self._get_content_data(limit)

        if not content_data:
            return TopicMap(
                clusters=[],
                connections=[],
                source_distribution={},
                date_distribution={},
                total_content=0,
                generated_at=datetime.now(),
            )

        # Extract topics using simple keyword clustering
        clusters = self._cluster_topics(content_data, min_cluster_size)

        # Find connections between clusters
        connections = self._find_connections(clusters)

        # Calculate distributions
        source_dist = Counter(c.get("source", "unknown") for c in content_data)
        date_dist = self._calculate_date_distribution(content_data)

        return TopicMap(
            clusters=clusters,
            connections=connections,
            source_distribution=dict(source_dist.most_common(20)),
            date_distribution=date_dist,
            total_content=len(content_data),
            generated_at=datetime.now(),
        )

    def _get_content_data(self, limit: int) -> List[Dict]:
        """Get content metadata from the vector store."""
        if not self.db_path.exists():
            return []

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("""
                SELECT DISTINCT content_id, metadata
                FROM chunks
                LIMIT ?
            """, (limit,)).fetchall()

        content_data = []
        for content_id, metadata_json in rows:
            try:
                metadata = json.loads(metadata_json) if metadata_json else {}
                metadata["content_id"] = content_id

                # Infer source from content_id
                if not metadata.get("source"):
                    metadata["source"] = self._infer_source(content_id)

                content_data.append(metadata)
            except Exception:
                continue

        return content_data

    def _infer_source(self, content_id: str) -> str:
        """Infer source type from content ID."""
        content_id_lower = content_id.lower()

        source_patterns = {
            "stratechery": "Stratechery",
            "acquired": "Acquired",
            "ezra": "Ezra Klein",
            "hard-fork": "Hard Fork",
            "dithering": "Dithering",
            "tyler": "Tyler Cowen",
            "lex": "Lex Fridman",
            "dwarkesh": "Dwarkesh Patel",
            "newsletter": "Newsletter",
            "article": "Article",
        }

        for pattern, source in source_patterns.items():
            if pattern in content_id_lower:
                return source

        return "Other"

    def _cluster_topics(
        self,
        content_data: List[Dict],
        min_cluster_size: int,
    ) -> List[TopicCluster]:
        """Cluster content by topic keywords."""
        import re

        # Extract keywords from titles
        keyword_to_content = defaultdict(list)

        # Common topic keywords to look for
        topic_keywords = [
            "ai", "artificial intelligence", "machine learning", "llm", "gpt",
            "apple", "google", "microsoft", "meta", "amazon", "nvidia",
            "startup", "venture", "vc", "funding",
            "crypto", "bitcoin", "blockchain", "web3",
            "china", "geopolitics", "regulation", "antitrust",
            "climate", "energy", "nuclear",
            "healthcare", "biotech", "longevity",
            "media", "streaming", "content", "creator",
            "education", "future", "work",
            "podcast", "interview", "conversation",
        ]

        for content in content_data:
            title = content.get("title", content.get("content_id", "")).lower()

            for keyword in topic_keywords:
                if keyword in title:
                    keyword_to_content[keyword].append(content)

        # Create clusters from keywords with enough content
        clusters = []
        for keyword, contents in keyword_to_content.items():
            if len(contents) >= min_cluster_size:
                sources = list(set(c.get("source", "unknown") for c in contents))

                clusters.append(TopicCluster(
                    id=keyword.replace(" ", "_"),
                    label=keyword.title(),
                    keywords=[keyword],
                    content_count=len(contents),
                    sources=sources[:5],
                    avg_date=None,
                ))

        # Sort by content count
        clusters.sort(key=lambda c: c.content_count, reverse=True)

        return clusters[:20]  # Top 20 clusters

    def _find_connections(self, clusters: List[TopicCluster]) -> List[Dict]:
        """Find connections between topic clusters."""
        connections = []

        # Simple: connect clusters that share sources
        for i, c1 in enumerate(clusters):
            for c2 in clusters[i+1:]:
                shared_sources = set(c1.sources) & set(c2.sources)
                if shared_sources:
                    connections.append({
                        "source": c1.id,
                        "target": c2.id,
                        "weight": len(shared_sources),
                        "shared": list(shared_sources),
                    })

        return connections

    def _calculate_date_distribution(self, content_data: List[Dict]) -> Dict[str, int]:
        """Calculate content distribution by month."""
        month_counts = Counter()

        for content in content_data:
            date_str = content.get("date") or content.get("published_date")
            if date_str:
                try:
                    # Parse various date formats
                    for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%dT%H:%M:%S"]:
                        try:
                            dt = datetime.strptime(date_str[:19], fmt)
                            month_key = dt.strftime("%Y-%m")
                            month_counts[month_key] += 1
                            break
                        except ValueError:
                            continue
                except Exception:
                    continue

        # Sort by date
        return dict(sorted(month_counts.items())[-12:])  # Last 12 months

    def generate_html(self, topic_map: TopicMap) -> str:
        """Generate an interactive HTML visualization."""
        clusters_json = json.dumps([
            {
                "id": c.id,
                "label": c.label,
                "count": c.content_count,
                "sources": c.sources,
            }
            for c in topic_map.clusters
        ])

        connections_json = json.dumps(topic_map.connections)
        sources_json = json.dumps(topic_map.source_distribution)
        dates_json = json.dumps(topic_map.date_distribution)

        html = f"""<!DOCTYPE html>
<html>
<head>
    <title>Atlas Topic Map</title>
    <script src="https://d3js.org/d3.v7.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            margin: 0;
            padding: 20px;
            background: #1a1a2e;
            color: #eee;
        }}
        h1, h2 {{ color: #fff; }}
        .container {{ max-width: 1400px; margin: 0 auto; }}
        .grid {{ display: grid; grid-template-columns: 2fr 1fr; gap: 20px; }}
        .card {{
            background: #16213e;
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 20px;
        }}
        .topic-cloud {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }}
        .topic {{
            background: #0f3460;
            padding: 8px 16px;
            border-radius: 20px;
            cursor: pointer;
            transition: all 0.2s;
        }}
        .topic:hover {{
            background: #e94560;
            transform: scale(1.05);
        }}
        .topic-count {{
            font-size: 0.8em;
            opacity: 0.7;
            margin-left: 5px;
        }}
        .chart-container {{ height: 300px; }}
        .stats {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 15px;
            margin-bottom: 20px;
        }}
        .stat {{
            background: #0f3460;
            padding: 20px;
            border-radius: 8px;
            text-align: center;
        }}
        .stat-value {{
            font-size: 2em;
            font-weight: bold;
            color: #e94560;
        }}
        .stat-label {{
            opacity: 0.7;
            font-size: 0.9em;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>Atlas Topic Map</h1>
        <p>Generated: {topic_map.generated_at.strftime('%Y-%m-%d %H:%M')}</p>

        <div class="stats">
            <div class="stat">
                <div class="stat-value">{topic_map.total_content:,}</div>
                <div class="stat-label">Total Content</div>
            </div>
            <div class="stat">
                <div class="stat-value">{len(topic_map.clusters)}</div>
                <div class="stat-label">Topic Clusters</div>
            </div>
            <div class="stat">
                <div class="stat-value">{len(topic_map.source_distribution)}</div>
                <div class="stat-label">Sources</div>
            </div>
        </div>

        <div class="grid">
            <div>
                <div class="card">
                    <h2>Topics</h2>
                    <div class="topic-cloud" id="topics"></div>
                </div>

                <div class="card">
                    <h2>Content Over Time</h2>
                    <div class="chart-container">
                        <canvas id="timeChart"></canvas>
                    </div>
                </div>
            </div>

            <div>
                <div class="card">
                    <h2>Sources</h2>
                    <div class="chart-container">
                        <canvas id="sourceChart"></canvas>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <script>
        const clusters = {clusters_json};
        const sources = {sources_json};
        const dates = {dates_json};

        // Render topic cloud
        const topicsDiv = document.getElementById('topics');
        clusters.forEach(c => {{
            const div = document.createElement('div');
            div.className = 'topic';
            div.innerHTML = `${{c.label}}<span class="topic-count">(${{c.count}})</span>`;
            div.title = `Sources: ${{c.sources.join(', ')}}`;
            topicsDiv.appendChild(div);
        }});

        // Source chart
        new Chart(document.getElementById('sourceChart'), {{
            type: 'doughnut',
            data: {{
                labels: Object.keys(sources),
                datasets: [{{
                    data: Object.values(sources),
                    backgroundColor: [
                        '#e94560', '#0f3460', '#533483', '#16213e',
                        '#1a1a2e', '#4a0e4e', '#81689d', '#ffd460'
                    ]
                }}]
            }},
            options: {{
                plugins: {{
                    legend: {{ labels: {{ color: '#fff' }} }}
                }}
            }}
        }});

        // Time chart
        new Chart(document.getElementById('timeChart'), {{
            type: 'bar',
            data: {{
                labels: Object.keys(dates),
                datasets: [{{
                    label: 'Content',
                    data: Object.values(dates),
                    backgroundColor: '#e94560'
                }}]
            }},
            options: {{
                scales: {{
                    y: {{ ticks: {{ color: '#fff' }} }},
                    x: {{ ticks: {{ color: '#fff' }} }}
                }},
                plugins: {{
                    legend: {{ display: false }}
                }}
            }}
        }});
    </script>
</body>
</html>"""
        return html

    def save_html(self, topic_map: TopicMap, output_path: Optional[Path] = None) -> Path:
        """Save the topic map as an HTML file."""
        if output_path is None:
            output_path = Path("data/topic_map.html")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        html = self.generate_html(topic_map)
        output_path.write_text(html)

        return output_path
