"""Learning graph mutations: edit and delete nodes in the learning graph.

Provides operations to modify the learning graph structure:
- Archive skills (via Curator)
- Edit memory entries (via MemoryEntryStore)
- Delete memory entries

This module depends on:
- Curator for skill archival
- MemoryEntryStore for memory mutations
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nanobot.agent.curator import Curator
    from nanobot.agent.tools.memory_tool import MemoryEntryStore


def archive_skill(curator: Curator, name: str) -> dict[str, Any]:
    """Archive a skill (set state to 'archived').

    This is a safe operation: the skill file is preserved, but it will be
    filtered out from list_skills().

    Args:
        curator: Curator instance.
        name: Skill name to archive.

    Returns:
        Dict with success status and message.
    """
    try:
        rec = curator.usage_store.load(name)
        if rec.state == "archived":
            return {
                "success": False,
                "error": f"Skill '{name}' is already archived.",
            }

        rec.state = "archived"
        curator.usage_store.save(rec)

        return {
            "success": True,
            "message": f"Skill '{name}' archived successfully.",
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to archive skill '{name}': {e}",
        }


def revive_skill(curator: Curator, name: str) -> dict[str, Any]:
    """Revive an archived or stale skill to ACTIVE state.

    Args:
        curator: Curator instance.
        name: Skill name to revive.

    Returns:
        Dict with success status and message.
    """
    try:
        success = curator.revive_skill(name)
        if success:
            return {
                "success": True,
                "message": f"Skill '{name}' revived to ACTIVE state.",
            }
        else:
            return {
                "success": False,
                "error": f"Skill '{name}' is already ACTIVE or not found.",
            }
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to revive skill '{name}': {e}",
        }


def edit_memory_entry(
    entry_store: MemoryEntryStore,
    old_text: str,
    new_content: str,
) -> dict[str, Any]:
    """Edit a memory entry by replacing old_text with new_content.

    Args:
        entry_store: MemoryEntryStore instance.
        old_text: Substring identifying the entry to edit.
        new_content: New content for the entry.

    Returns:
        Dict with success status and message.
    """
    try:
        result = entry_store.replace(old_text, new_content)
        return result
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to edit memory entry: {e}",
        }


def delete_memory_entry(
    entry_store: MemoryEntryStore,
    old_text: str,
) -> dict[str, Any]:
    """Delete a memory entry.

    Args:
        entry_store: MemoryEntryStore instance.
        old_text: Substring identifying the entry to delete.

    Returns:
        Dict with success status and message.
    """
    try:
        result = entry_store.remove(old_text)
        return result
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to delete memory entry: {e}",
        }


def add_memory_entry(
    entry_store: MemoryEntryStore,
    content: str,
) -> dict[str, Any]:
    """Add a new memory entry.

    Args:
        entry_store: MemoryEntryStore instance.
        content: Content for the new entry.

    Returns:
        Dict with success status and message.
    """
    try:
        result = entry_store.add(content)
        return result
    except Exception as e:
        return {
            "success": False,
            "error": f"Failed to add memory entry: {e}",
        }


def get_node_details(
    curator: Curator,
    entry_store: MemoryEntryStore | None,
    node_type: str,
    node_id: str | int,
) -> dict[str, Any]:
    """Get detailed information about a node (skill or memory card).

    Args:
        curator: Curator instance.
        entry_store: Optional MemoryEntryStore instance.
        node_type: "skill" or "memory".
        node_id: Skill name (str) or memory card index (int).

    Returns:
        Dict with node details.
    """
    if node_type == "skill":
        try:
            rec = curator.usage_store.load(str(node_id))
            return {
                "success": True,
                "type": "skill",
                "name": rec.name,
                "use_count": rec.use_count,
                "view_count": rec.view_count,
                "state": rec.state,
                "created_by": rec.created_by,
                "pinned": rec.pinned,
                "last_activity_at": rec.last_activity_at,
                "created_at": rec.created_at,
            }
        except Exception as e:
            return {
                "success": False,
                "error": f"Failed to get skill details: {e}",
            }

    elif node_type == "memory":
        if entry_store is None:
            return {
                "success": False,
                "error": "MemoryEntryStore not available.",
            }
        try:
            idx = int(node_id)
            if 0 <= idx < len(entry_store.entries):
                return {
                    "success": True,
                    "type": "memory",
                    "index": idx,
                    "content": entry_store.entries[idx],
                }
            else:
                return {
                    "success": False,
                    "error": f"Memory card index {idx} out of range.",
                }
        except (ValueError, IndexError) as e:
            return {
                "success": False,
                "error": f"Invalid memory card index: {e}",
            }

    else:
        return {
            "success": False,
            "error": f"Unknown node type: {node_type}",
        }
