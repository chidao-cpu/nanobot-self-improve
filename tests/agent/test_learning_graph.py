"""Tests for learning_graph.py - skill and memory relationship visualization."""

import pytest
from pathlib import Path
from unittest.mock import Mock

from nanobot.agent.learning_graph import (
    SkillNode,
    MemoryCard,
    build_skill_nodes,
    build_edges,
    _memory_cards,
    _tokenize,
    _memory_skill_edges,
    build_learning_graph,
)
from nanobot.agent.skill_usage import SkillUsageRecord, STATE_ACTIVE


@pytest.fixture
def mock_skills_loader():
    """Create a mock SkillsLoader."""
    loader = Mock()
    loader.list_skills.return_value = [
        {"name": "skill-a", "source": "workspace"},
        {"name": "skill-b", "source": "workspace"},
        {"name": "skill-c", "source": "builtin"},
    ]
    loader.get_skill_metadata.side_effect = lambda name: {
        "skill-a": {"category": "coding", "related": ["skill-b"]},
        "skill-b": {"category": "coding", "related": ["skill-a"]},
        "skill-c": {"category": "general"},
    }.get(name, {})
    return loader


@pytest.fixture
def mock_usage_store():
    """Create a mock SkillUsageStore."""
    store = Mock()
    
    def load_side_effect(name):
        return SkillUsageRecord(
            name=name,
            use_count=10,
            state=STATE_ACTIVE,
            created_by="user",
            pinned=False,
        )
    
    store.load.side_effect = load_side_effect
    return store


@pytest.fixture
def mock_memory_store():
    """Create a mock MemoryEntryStore."""
    store = Mock()
    store.entries = [
        "User prefers Python for coding tasks",
        "Project uses TypeScript and React",
        "Skill-a is useful for code review",
    ]
    return store


def test_build_skill_nodes(mock_skills_loader, mock_usage_store):
    """Test building skill nodes from loader and usage store."""
    nodes = build_skill_nodes(mock_skills_loader, mock_usage_store)
    
    assert len(nodes) == 3
    assert nodes[0].name == "skill-a"
    assert nodes[0].category == "coding"
    assert nodes[0].use_count == 10
    assert nodes[0].related == ["skill-b"]


def test_build_edges():
    """Test building skill-skill edges from related field."""
    nodes = [
        SkillNode(
            name="skill-a",
            category="coding",
            source="workspace",
            use_count=10,
            state="active",
            created_by="user",
            pinned=False,
            related=["skill-b"],
        ),
        SkillNode(
            name="skill-b",
            category="coding",
            source="workspace",
            use_count=5,
            state="active",
            created_by="user",
            pinned=False,
            related=["skill-a"],
        ),
    ]
    
    edges = build_edges(nodes)
    
    # Should have one edge (skill-a, skill-b), not duplicated
    assert len(edges) == 1
    assert ("skill-a", "skill-b") in edges or ("skill-b", "skill-a") in edges


def test_memory_cards(mock_memory_store):
    """Test extracting memory cards from MemoryEntryStore."""
    cards = _memory_cards(mock_memory_store)
    
    assert len(cards) == 3
    assert cards[0].content == "User prefers Python for coding tasks"
    assert cards[0].index == 0
    assert cards[2].content == "Skill-a is useful for code review"
    assert cards[2].index == 2


def test_tokenize():
    """Test tokenization function."""
    text = "Hello, World! This is a test."
    tokens = _tokenize(text)
    
    assert "hello" in tokens
    assert "world" in tokens
    assert "test" in tokens
    assert len(tokens) == 6


def test_memory_skill_edges(mock_memory_store):
    """Test building memory-skill edges based on lexical overlap."""
    cards = _memory_cards(mock_memory_store)
    nodes = [
        SkillNode(
            name="skill-a",
            category="coding",
            source="workspace",
            use_count=10,
            state="active",
            created_by="user",
            pinned=False,
            related=[],
        ),
    ]
    
    edges = _memory_skill_edges(cards, nodes, top_k=2)
    
    # Card 2 mentions "skill-a" explicitly, should have highest score
    assert len(edges) > 0
    card_indices = [e[0] for e in edges]
    assert 2 in card_indices  # Card with "Skill-a" should be connected


def test_build_learning_graph(mock_skills_loader, mock_usage_store, mock_memory_store):
    """Test building complete learning graph."""
    graph = build_learning_graph(mock_skills_loader, mock_usage_store, mock_memory_store)
    
    assert "skills" in graph
    assert "skill_edges" in graph
    assert "memory_cards" in graph
    assert "memory_skill_edges" in graph
    
    assert len(graph["skills"]) == 3
    assert len(graph["memory_cards"]) == 3
    assert len(graph["skill_edges"]) >= 0
    assert len(graph["memory_skill_edges"]) >= 0


def test_build_learning_graph_without_memory(mock_skills_loader, mock_usage_store):
    """Test building learning graph without memory store."""
    graph = build_learning_graph(mock_skills_loader, mock_usage_store, None)
    
    assert len(graph["skills"]) == 3
    assert len(graph["memory_cards"]) == 0
    assert len(graph["memory_skill_edges"]) == 0
