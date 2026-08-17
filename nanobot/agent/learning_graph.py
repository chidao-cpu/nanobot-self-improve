"""Learning graph: skill and memory relationship visualization.

Builds a graph of skills and memory entries with edges based on lexical overlap.
This enables visualization of the agent's knowledge structure.

Depends on Part 2 (memory system) being complete: MEMORY.md uses §-delimited
entries managed by MemoryEntryStore.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nanobot.agent.skills import SkillsLoader
    from nanobot.agent.skill_usage import SkillUsageStore
    from nanobot.agent.tools.memory_tool import MemoryEntryStore


@dataclass
class SkillNode:
    """A skill in the learning graph."""

    name: str
    category: str
    source: str  # workspace | builtin
    use_count: int
    state: str
    created_by: str
    pinned: bool
    related: list[str]


@dataclass
class MemoryCard:
    """A memory entry in the learning graph."""

    content: str
    index: int


def build_skill_nodes(
    loader: SkillsLoader,
    usage_store: SkillUsageStore,
) -> list[SkillNode]:
    """Build skill nodes from SkillsLoader and SkillUsageStore.

    Args:
        loader: SkillsLoader instance.
        usage_store: SkillUsageStore instance.

    Returns:
        List of SkillNode objects.
    """
    skills = loader.list_skills(filter_unavailable=False)
    nodes = []

    for entry in skills:
        name = entry["name"]
        source = entry["source"]

        # Get usage record
        try:
            rec = usage_store.load(name)
            use_count = rec.use_count
            state = rec.state
            created_by = rec.created_by
            pinned = rec.pinned
        except Exception:
            use_count = 0
            state = "active"
            created_by = "user"
            pinned = False

        # Get metadata for category
        meta = loader.get_skill_metadata(name) or {}
        category = meta.get("category", "general")

        # Get related skills from metadata
        related = meta.get("related", [])
        if not isinstance(related, list):
            related = []

        nodes.append(SkillNode(
            name=name,
            category=category,
            source=source,
            use_count=use_count,
            state=state,
            created_by=created_by,
            pinned=pinned,
            related=related,
        ))

    return nodes


def build_edges(nodes: list[SkillNode]) -> list[tuple[str, str]]:
    """Build skill-skill edges from the 'related' field.

    Args:
        nodes: List of SkillNode objects.

    Returns:
        List of (source, target) tuples representing edges.
    """
    edges = []
    node_names = {node.name for node in nodes}

    for node in nodes:
        for related_name in node.related:
            if related_name in node_names:
                # Avoid duplicate edges (A→B and B→A)
                edge = tuple(sorted([node.name, related_name]))
                if edge not in edges:
                    edges.append(edge)

    return edges


def _memory_cards(entry_store: MemoryEntryStore) -> list[MemoryCard]:
    """Extract memory cards from MemoryEntryStore.

    MEMORY.md uses §-delimited entries. MemoryEntryStore.entries contains
    the raw entries without rendering decorations.

    Args:
        entry_store: MemoryEntryStore instance.

    Returns:
        List of MemoryCard objects.
    """
    cards = []
    for idx, content in enumerate(entry_store.entries):
        cards.append(MemoryCard(content=content, index=idx))
    return cards


def _tokenize(text: str) -> set[str]:
    """Simple tokenizer: split on whitespace and punctuation, lowercase."""
    tokens = re.findall(r'\w+', text.lower())
    return set(tokens)


def _memory_skill_edges(
    cards: list[MemoryCard],
    skill_nodes: list[SkillNode],
    top_k: int = 4,
) -> list[tuple[int, str, float]]:
    """Build memory-skill edges based on lexical overlap.

    Scoring:
    - Skill name appears in card: +6
    - Token overlap: +1 per token

    Args:
        cards: List of MemoryCard objects.
        skill_nodes: List of SkillNode objects.
        top_k: Maximum number of edges per memory card.

    Returns:
        List of (card_index, skill_name, score) tuples.
    """
    edges = []

    for card in cards:
        card_tokens = _tokenize(card.content)
        scores = []

        for node in skill_nodes:
            score = 0.0

            # Skill name appears in card
            if node.name.lower() in card.content.lower():
                score += 6.0

            # Token overlap
            node_tokens = _tokenize(node.name)
            overlap = card_tokens & node_tokens
            score += len(overlap)

            if score > 0:
                scores.append((node.name, score))

        # Take top-k edges
        scores.sort(key=lambda x: x[1], reverse=True)
        for skill_name, score in scores[:top_k]:
            edges.append((card.index, skill_name, score))

    return edges


def build_learning_graph(
    loader: SkillsLoader,
    usage_store: SkillUsageStore,
    entry_store: MemoryEntryStore | None = None,
) -> dict[str, Any]:
    """Build the complete learning graph.

    Args:
        loader: SkillsLoader instance.
        usage_store: SkillUsageStore instance.
        entry_store: Optional MemoryEntryStore instance.

    Returns:
        Dict with nodes, edges, and memory-skill edges.
    """
    skill_nodes = build_skill_nodes(loader, usage_store)
    skill_edges = build_edges(skill_nodes)

    memory_cards = []
    memory_skill_edges_list = []

    if entry_store is not None:
        memory_cards = _memory_cards(entry_store)
        memory_skill_edges_list = _memory_skill_edges(memory_cards, skill_nodes)

    return {
        "skills": [
            {
                "name": node.name,
                "category": node.category,
                "source": node.source,
                "use_count": node.use_count,
                "state": node.state,
                "created_by": node.created_by,
                "pinned": node.pinned,
            }
            for node in skill_nodes
        ],
        "skill_edges": [
            {"source": src, "target": tgt}
            for src, tgt in skill_edges
        ],
        "memory_cards": [
            {"index": card.index, "content": card.content}
            for card in memory_cards
        ],
        "memory_skill_edges": [
            {"card_index": idx, "skill": skill, "score": score}
            for idx, skill, score in memory_skill_edges_list
        ],
    }
