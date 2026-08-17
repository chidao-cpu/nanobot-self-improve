"""Learn prompt builder: constructs prompts for skill creation.

Used by the /learn command to guide the agent through researching a topic
and creating a new skill (SKILL.md) with proper structure.
"""

from __future__ import annotations


def build_learn_prompt(user_request: str, skill_creator_standards: str) -> str:
    """Build a prompt for learning a new skill.

    Args:
        user_request: The user's request describing what to learn.
        skill_creator_standards: The skill-creator SKILL.md content defining
            authoring standards.

    Returns:
        A complete prompt guiding the agent through skill creation.
    """
    return f"""You are about to LEARN a new reusable skill from this request:
<request>{user_request}</request>

Follow these steps:
1. **Gather sources**: Use read_file / search / web_fetch to collect authoritative material about the topic.
2. **Distill**: Extract only the durable, reusable procedure (not one-off details or temporary state).
3. **Author**: Call skill_manage(action="create", name=..., content=...) to persist a SKILL.md.

Authoring standards:
{skill_creator_standards}

Important:
- The skill should be reusable across sessions, not tied to a specific conversation.
- Include clear trigger conditions (when to use this skill).
- Provide step-by-step instructions that another agent instance can follow.
- Keep it concise but complete enough to be actionable.
"""
