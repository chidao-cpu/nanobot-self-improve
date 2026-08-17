"""Background memory and skill review: automatic consolidation after each turn.

Implements Hermes-style background review that forks a lightweight agent
to review conversation history and proactively save important facts to memory
and update/create skills when reusable procedures emerge.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

from nanobot.agent.runner import AgentRunner, AgentRunSpec
from nanobot.agent.skill_provenance import (
    BACKGROUND_REVIEW,
    reset_background_review_read_marks,
    set_current_write_origin,
    reset_current_write_origin,
)
from nanobot.agent.tools.memory_tool import MemoryEntryStore, MemoryTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.utils.llm_runtime import LLMRuntime

if TYPE_CHECKING:
    from nanobot.agent.skill_usage import SkillUsageStore
    from nanobot.agent.skills import SkillsLoader
    from nanobot.session.manager import Session


# Review prompts adapted from Hermes for active skill/memory curation
_MEMORY_REVIEW_PROMPT = (
    "Review the conversation above and consider saving to memory if appropriate.\n\n"
    "Focus on:\n"
    "1. Has the user revealed things about themselves — their persona, desires, "
    "preferences, or personal details worth remembering?\n"
    "2. Has the user expressed expectations about how you should behave, their work "
    "style, or ways they want you to operate?\n\n"
    "If something stands out, save it using the memory tool. "
    "If nothing is worth saving, just say 'Nothing to save.' and stop."
)

_SKILL_REVIEW_PROMPT = (
    "Review the conversation above and update the skill library. Be "
    "ACTIVE — most sessions produce at least one skill update, even if "
    "small. A pass that does nothing is a missed learning opportunity, "
    "not a neutral outcome.\n\n"
    "Target shape of the library: CLASS-LEVEL skills, each with a rich "
    "SKILL.md and a `references/` directory for session-specific detail. "
    "Not a long flat list of narrow one-session-one-skill entries. This "
    "shapes HOW you update, not WHETHER you update.\n\n"
    "Signals to look for (any one of these warrants action):\n"
    "  • User corrected your style, tone, format, legibility, or "
    "verbosity. Frustration signals like 'stop doing X', 'this is too "
    "verbose', 'don't format like this', 'why are you explaining', "
    "'just give me the answer', 'you always do Y and I hate it', or an "
    "explicit 'remember this' are FIRST-CLASS skill signals, not just "
    "memory signals. Update the relevant skill(s) to embed the "
    "preference so the next session starts already knowing.\n"
    "  • User corrected your workflow, approach, or sequence of steps. "
    "Encode the correction as a pitfall or explicit step in the skill "
    "that governs that class of task.\n"
    "  • Non-trivial technique, fix, workaround, debugging path, or "
    "tool-usage pattern emerged that a future session would benefit "
    "from. Capture it.\n"
    "  • A skill that got loaded or consulted this session turned out "
    "to be wrong, missing a step, or outdated. Patch it NOW.\n\n"
    "Preference order — prefer the earliest action that fits, but do "
    "pick one when a signal above fired:\n"
    "  1. UPDATE A CURRENTLY-LOADED SKILL. Look back through the "
    "conversation for skills the user loaded via /skill-name or you "
    "read via skill_view. If any of them covers the territory of the "
    "new learning, PATCH that one first. It is the skill that was in "
    "play, so it's the right one to extend — but only if it is "
    "curator-managed. Bundled, hub, pinned, and user-owned skills are "
    "off-limits to you no matter how relevant (see Protected skills "
    "below); for those, fall through to the next option.\n"
    "  2. UPDATE AN EXISTING UMBRELLA (via skills_list + skill_view). "
    "If no loaded skill fits but an existing class-level skill does, "
    "patch it. Add a subsection, a pitfall, or broaden a trigger.\n"
    "  3. ADD A SUPPORT FILE under an existing umbrella. Skills can be "
    "packaged with three kinds of support files — use the right "
    "directory per kind:\n"
    "     • `references/<topic>.md` — session-specific detail (error "
    "transcripts, reproduction recipes, provider quirks) AND "
    "condensed knowledge banks: quoted research, API docs, external "
    "authoritative excerpts, or domain notes you found while working "
    "on the problem. Write it concise and for the value of the task, "
    "not as a full mirror of upstream docs.\n"
    "     • `templates/<name>.<ext>` — starter files meant to be "
    "copied and modified (boilerplate configs, scaffolding, a "
    "known-good example the agent can `reproduce with modifications`).\n"
    "     • `scripts/<name>.<ext>` — statically re-runnable actions "
    "the skill can invoke directly (verification scripts, fixture "
    "generators, deterministic probes, anything the agent should run "
    "rather than hand-type each time).\n"
    "     Add support files via skill_manage action=write_file with "
    "file_path starting 'references/', 'templates/', or 'scripts/'. "
    "The umbrella's SKILL.md should gain a one-line pointer to any "
    "new support file so future agents know it exists.\n"
    "  4. CREATE A NEW CLASS-LEVEL UMBRELLA SKILL when no existing "
    "skill covers the class. The name MUST be at the class level. "
    "The name MUST NOT be a specific PR number, error string, feature "
    "codename, library-alone name, or 'fix-X / debug-Y / audit-Z-today' "
    "session artifact. If the proposed name only makes sense for "
    "today's task, it's wrong — fall back to (1), (2), or (3).\n\n"
    "User-preference embedding (important): when the user expressed a "
    "style/format/workflow preference, the update belongs in the "
    "SKILL.md body, not just in memory. Memory captures 'who the user "
    "is and what the current situation and state of your operations "
    "are'; skills capture 'how to do this class of task for this "
    "user'. When they complain about how you handled a task, the "
    "skill that governs that task needs to carry the lesson.\n\n"
    "If you notice two existing skills that overlap, note it in your "
    "reply — the background curator handles consolidation at scale.\n\n"
    "Protected skills (DO NOT edit these):\n"
    "  • Bundled skills (shipped with nanobot).\n"
    "  • Hub-installed skills.\n"
    "  • Skills in skills.external_dirs (externally owned).\n"
    "  • PINNED skills. You are an autonomous no-user-present actor, "
    "so pin blocks your writes too — content updates included. Only "
    "the user, in a foreground session, can change a pinned skill.\n"
    "  • USER-OWNED skills — anything not curator-managed. A skill the "
    "user hand-wrote, installed by URL, or asked a foreground agent to "
    "create is theirs, not yours; your writes to it WILL be refused. "
    "This includes skills that were loaded or consulted this session: "
    "being in play does not make one yours to edit. If such a skill is "
    "wrong or outdated, say so in your reply and recommend the user "
    "adopt it — do not try to patch it.\n"
    "If the only skills that need updating are protected, say\n"
    "'Nothing to save.' and stop.\n\n"
    "Do NOT capture (these become persistent self-imposed constraints "
    "that bite you later when the environment changes):\n"
    "  • Environment-dependent failures: missing binaries, fresh-install "
    "errors, post-migration path mismatches, 'command not found', "
    "unconfigured credentials, uninstalled packages. The user can fix "
    "these — they are not durable rules.\n"
    "  • Negative claims about tools or features ('browser tools do not "
    "work', 'X tool is broken', 'cannot use Y from execute_code'). These "
    "harden into refusals the agent cites against itself for months "
    "after the actual problem was fixed.\n"
    "  • Session-specific transient errors that resolved before the "
    "conversation ended. If retrying worked, the lesson is the retry "
    "pattern, not the original failure.\n"
    "  • One-off task narratives. A user asking 'summarize today's "
    "market' or 'analyze this PR' is not a class of work that warrants "
    "a skill.\n\n"
    "  • Unresolved failures: if the session ended WITHOUT actually "
    "finding a working method — you tried several things, none worked, "
    "and told the user to check manually — do NOT write those attempts "
    "up as a 'reliable workflow' or 'recommended approach'. That presents "
    "an untested sequence of failures as validated guidance a future "
    "session will trust and repeat. Either say 'Nothing to save', or, "
    "only if you are independently confident of a real working alternative "
    "(not something you are merely guessing might work), capture ONLY that "
    "alternative — never the dead ends, and never dressed up as best practice.\n\n"
    "If a tool failed because of setup state, capture the FIX (install "
    "command, config step, env var to set) under an existing setup or "
    "troubleshooting skill — never 'this tool does not work' as a "
    "standalone constraint.\n\n"
    "'Nothing to save.' is a real option but should NOT be the "
    "default. If the session ran smoothly with no corrections and "
    "produced no new technique, just say 'Nothing to save.' and stop. "
    "Otherwise, act."
)

_COMBINED_REVIEW_PROMPT = (
    "Review the conversation above and update two things:\n\n"
    "**Memory**: who the user is. Did the user reveal persona, "
    "desires, preferences, personal details, or expectations about "
    "how you should behave? Save facts about the user and durable "
    "preferences with the memory tool.\n\n"
    "**Skills**: how to do this class of task. Be ACTIVE — most "
    "sessions produce at least one skill update. A pass that does "
    "nothing is a missed learning opportunity, not a neutral outcome.\n\n"
    "Target shape of the skill library: CLASS-LEVEL skills with a rich "
    "SKILL.md and a `references/` directory for session-specific detail. "
    "Not a long flat list of narrow one-session-one-skill entries.\n\n"
    "Signals that warrant a skill update (any one is enough):\n"
    "  • User corrected your style, tone, format, legibility, "
    "verbosity, or approach. Frustration is a FIRST-CLASS skill "
    "signal, not just a memory signal. 'stop doing X', 'don't format "
    "like this', 'I hate when you Y' — embed the lesson in the skill "
    "that governs that task so the next session starts fixed.\n"
    "  • Non-trivial technique, fix, workaround, or debugging path "
    "emerged.\n"
    "  • A skill that was loaded or consulted turned out wrong, "
    "missing, or outdated — patch it now.\n\n"
    "Preference order for skills — pick the earliest that fits:\n"
    "  1. UPDATE A CURRENTLY-LOADED SKILL. Check what skills were "
    "loaded via /skill-name or skill_view in the conversation. If one "
    "of them covers the learning, PATCH it first. It was in play; "
    "it's the right place — provided it is curator-managed. Protected "
    "and user-owned skills are off-limits however relevant; fall "
    "through when one of those is the best fit.\n"
    "  2. UPDATE AN EXISTING UMBRELLA (skills_list + skill_view to "
    "find the right one). Patch it.\n"
    "  3. ADD A SUPPORT FILE under an existing umbrella via "
    "skill_manage action=write_file. Three kinds: "
    "`references/<topic>.md` for session-specific detail OR condensed "
    "knowledge banks (quoted research, API docs excerpts, domain "
    "notes) written concise and task-focused; `templates/<name>.<ext>` "
    "for starter files meant to be copied and modified; "
    "`scripts/<name>.<ext>` for statically re-runnable actions "
    "(verification, fixture generators, probes). Add a one-line "
    "pointer in SKILL.md so future agents find them.\n"
    "  4. CREATE A NEW CLASS-LEVEL UMBRELLA when nothing exists. "
    "Name at the class level — NOT a PR number, error string, "
    "codename, library-alone name, or 'fix-X / debug-Y' session "
    "artifact. If the name only fits today's task, fall back to (1), "
    "(2), or (3).\n\n"
    "User-preference embedding: when the user complains about how "
    "you handled a task, update the skill that governs that task — "
    "memory alone isn't enough. Memory says 'who the user is and "
    "what the current situation and state of your operations are'; "
    "skills say 'how to do this class of task for this user'. Both "
    "should carry user-preference lessons when relevant.\n\n"
    "If you notice overlapping existing skills, mention it — the "
    "background curator handles consolidation.\n\n"
    "Protected skills (DO NOT edit these):\n"
    "  • Bundled skills (shipped with nanobot).\n"
    "  • Hub-installed skills.\n"
    "  • Skills in skills.external_dirs (externally owned).\n"
    "  • PINNED skills. Pin blocks autonomous writes entirely — "
    "content updates included — because no user is present to consent. "
    "Only a foreground session can change one.\n"
    "  • USER-OWNED skills — anything not curator-managed (hand-written, "
    "URL-installed, or created by a foreground agent at the user's "
    "request). Your writes to these WILL be refused, including to skills "
    "loaded or consulted this session. If one is wrong, say so in your "
    "reply and recommend the user adopt it instead.\n"
    "If the only skills that need updating are protected, say\n"
    "'Nothing to save.' and stop.\n\n"
    "Do NOT capture as skills (these become persistent self-imposed "
    "constraints that bite you later when the environment changes):\n"
    "  • Environment-dependent failures: missing binaries, fresh-install "
    "errors, post-migration path mismatches, 'command not found', "
    "unconfigured credentials, uninstalled packages. The user can fix "
    "these — they are not durable rules.\n"
    "  • Negative claims about tools or features ('browser tools do not "
    "work', 'X tool is broken', 'cannot use Y from execute_code'). These "
    "harden into refusals the agent cites against itself for months "
    "after the actual problem was fixed.\n"
    "  • Session-specific transient errors that resolved before the "
    "conversation ended. If retrying worked, the lesson is the retry "
    "pattern, not the original failure.\n"
    "  • One-off task narratives. A user asking 'summarize today's "
    "market' or 'analyze this PR' is not a class of work that warrants "
    "a skill.\n\n"
    "  • Unresolved failures: if the session ended WITHOUT actually "
    "finding a working method — you tried several things, none worked, "
    "and told the user to check manually — do NOT write those attempts "
    "up as a 'reliable workflow' or 'recommended approach'. That presents "
    "an untested sequence of failures as validated guidance a future "
    "session will trust and repeat. Either say 'Nothing to save', or, "
    "only if you are independently confident of a real working alternative "
    "(not something you are merely guessing might work), capture ONLY that "
    "alternative — never the dead ends, and never dressed up as best practice.\n\n"
    "If a tool failed because of setup state, capture the FIX (install "
    "command, config step, env var to set) under an existing setup or "
    "troubleshooting skill — never 'this tool does not work' as a "
    "standalone constraint.\n\n"
    "Act on whichever of the two dimensions has real signal. If "
    "genuinely nothing stands out on either, say 'Nothing to save.' "
    "and stop — but don't reach for that conclusion as a default."
)


class BackgroundReviewer:
    """Forks a lightweight agent to review conversation and update memory/skills.
    
    Triggers every N tool iterations (controlled by skill_nudge_interval).
    The fork agent:
    - Inherits the parent's provider/model/runtime for cache warmth
    - Gets only memory and skill tools (restricted toolset)
    - Replays conversation history
    - Decides whether to call memory(action="add/replace/remove", ...)
    - Decides whether to create/update skills for reusable procedures
    """

    def __init__(
        self,
        workspace: Path,
        memory_entry_store: MemoryEntryStore | None,
        skills_loader: SkillsLoader | None = None,
        skill_usage_store: SkillUsageStore | None = None,
        skill_nudge_interval: int = 4,
    ):
        self.workspace = workspace
        self._memory_entry_store = memory_entry_store
        self._skills_loader = skills_loader
        self._skill_usage_store = skill_usage_store
        self._runner = AgentRunner()
        self._skill_nudge_interval = skill_nudge_interval
        self._iters_since_skill = 0  # Accumulated tool iterations since last review

    async def maybe_review(
        self,
        session: Session,
        runtime: LLMRuntime,
        *,
        tool_iterations: int = 0,
        max_messages: int = 50,
    ) -> bool:
        """Review recent conversation and optionally update memory/skills.
        
        Accumulates tool_iterations and only triggers a review when the
        accumulated count reaches skill_nudge_interval (Hermes-style).
        
        Args:
            session: The session to review
            runtime: LLM runtime to use (inherited from parent agent)
            tool_iterations: Number of tool iterations from this turn
            max_messages: Maximum number of recent messages to review
            
        Returns:
            True if a review was triggered, False otherwise
        """
        # Accumulate tool iterations
        if self._skill_nudge_interval <= 0:
            return False
        self._iters_since_skill += tool_iterations
        if self._iters_since_skill < self._skill_nudge_interval:
            logger.debug(
                "Background review deferred: {}/{} tool iterations",
                self._iters_since_skill,
                self._skill_nudge_interval,
            )
            return False
        # Reset counter
        self._iters_since_skill = 0

        has_memory = self._memory_entry_store is not None
        has_skills = self._skills_loader is not None and self._skill_usage_store is not None
        if not has_memory and not has_skills:
            logger.debug("Background review skipped: no memory or skill tools available")
            return False

        # Get recent messages from session
        messages = session.messages
        if not messages:
            logger.debug("Background review skipped: no messages in session")
            return False

        # Take only recent messages to avoid token bloat
        review_messages = messages[-max_messages:] if len(messages) > max_messages else messages
        
        # Build conversation context for review
        conversation_text = self._format_conversation(review_messages)
        if not conversation_text.strip():
            logger.debug("Background review skipped: no conversation content")
            return False

        # Build fork agent's message list
        fork_messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self._build_review_system_prompt(),
            },
            {
                "role": "user",
                "content": f"Conversation to review:\n\n{conversation_text}\n\n{_COMBINED_REVIEW_PROMPT}",
            },
        ]

        # Build restricted tool registry (memory + skills)
        tools = self._build_review_tools()

        # Set provenance: mark this fork as background_review origin
        # so skill_manager guards can restrict autonomous writes
        reset_background_review_read_marks()
        origin_token = set_current_write_origin(BACKGROUND_REVIEW)

        # Run fork agent in background
        try:
            logger.debug("Starting background review for session {}", session.key)
            result = await self._runner.run(
                AgentRunSpec(
                    initial_messages=fork_messages,
                    tools=tools,
                    runtime=runtime,
                    max_iterations=16,  # Hermes-style: more iterations for thorough review
                    max_tool_result_chars=8000,
                    error_message=None,  # Don't surface errors to user
                )
            )
            
            if result.tools_used:
                logger.info(
                    "Background review completed: {} tool(s) used ({})",
                    len(result.tools_used),
                    ", ".join(result.tools_used),
                )
                return True
            else:
                logger.debug("Background review completed: no updates needed")
                return False
                
        except Exception as e:
            # Background review failures should never break the main flow
            logger.warning("Background review failed: {}", e)
            return False
        finally:
            reset_current_write_origin(origin_token)
            reset_background_review_read_marks()

    def _build_review_system_prompt(self) -> str:
        """Build system prompt for the review fork agent.
        
        Hermes-style: detailed, action-oriented, with 'Be ACTIVE' attitude.
        """
        has_memory = self._memory_entry_store is not None
        has_skills = self._skills_loader is not None and self._skill_usage_store is not None

        parts: list[str] = []
        parts.append(
            "You are a background review agent. Your job is to examine a conversation "
            "and proactively update persistent memory and/or the skill library so that "
            "future sessions start smarter.\n\n"
            "Be ACTIVE — most sessions produce at least one update, even if small. "
            "A pass that does nothing is a missed learning opportunity, not a neutral outcome."
        )

        if has_memory:
            parts.append(
                "\n\n## Memory Guidelines\n"
                "You have access to the `memory` tool to save durable facts that persist "
                "across sessions.\n\n"
                "Focus on:\n"
                "1. Has the user revealed things about themselves — their persona, desires, "
                "preferences, or personal details worth remembering?\n"
                "2. Has the user expressed expectations about how you should behave, their "
                "work style, or ways they want you to operate?\n\n"
                "Rules:\n"
                "- Save user preferences, corrections, and personal details\n"
                "- Save stable facts about their environment, tools, or workflow\n"
                "- Do NOT save trivial information, task progress, or temporary state\n"
                "- Keep entries concise and high-signal\n"
                "- If memory is full, consolidate overlapping entries before adding new ones\n"
                "- Memory captures 'who the user is and what the current situation and state "
                "of your operations are'"
            )

        if has_skills:
            parts.append(
                "\n\n## Skill Guidelines\n"
                "You have access to `skills` (read-only: list/view) and `skill_manager` "
                "(create/patch/delete/write_file) tools.\n\n"
                "Target shape of the library: CLASS-LEVEL skills, each with a rich SKILL.md "
                "and a `references/` directory for session-specific detail. Not a long flat "
                "list of narrow one-session-one-skill entries.\n\n"
                "### Signals to look for (any one warrants action):\n"
                "- User corrected your style, tone, format, legibility, or verbosity. "
                "Frustration signals like 'stop doing X', 'this is too verbose', 'don't "
                "format like this', 'just give me the answer' are FIRST-CLASS skill signals. "
                "Update the relevant skill(s) to embed the preference.\n"
                "- User corrected your workflow, approach, or sequence of steps. Encode the "
                "correction as a pitfall or explicit step in the skill.\n"
                "- Non-trivial technique, fix, workaround, debugging path, or tool-usage "
                "pattern emerged that a future session would benefit from.\n"
                "- A skill that got loaded or consulted this session turned out wrong, "
                "missing a step, or outdated. Patch it NOW.\n\n"
                "### Preference order (pick the earliest that fits):\n"
                "1. UPDATE A CURRENTLY-LOADED SKILL. Check what skills were loaded via "
                "/skill-name or skill_view in the conversation. If one covers the learning, "
                "PATCH it first — it was in play, so it's the right place.\n"
                "2. UPDATE AN EXISTING UMBRELLA (skills(action='list') + skills(action='view') "
                "to find the right one). Patch it.\n"
                "3. ADD A SUPPORT FILE under an existing umbrella via "
                "skill_manager(action='write_file'). Three kinds:\n"
                "   - `references/<topic>.md` — session-specific detail OR condensed "
                "knowledge banks (quoted research, API docs excerpts, domain notes).\n"
                "   - `templates/<name>.<ext>` — starter files meant to be copied and "
                "modified.\n"
                "   - `scripts/<name>.<ext>` — statically re-runnable actions (verification, "
                "fixture generators, probes).\n"
                "4. CREATE A NEW CLASS-LEVEL UMBRELLA when nothing exists. Name at the class "
                "level — NOT a PR number, error string, codename, or 'fix-X / debug-Y' "
                "session artifact.\n\n"
                "### User-preference embedding:\n"
                "When the user complains about how you handled a task, update the skill that "
                "governs that task — memory alone isn't enough. Memory says 'who the user is'; "
                "skills say 'how to do this class of task for this user'. Both should carry "
                "user-preference lessons when relevant.\n\n"
                "### Protected skills (DO NOT edit these):\n"
                "- Builtin skills (source: builtin)\n"
                "- Pinned skills\n"
                "- Skills not created by the agent (created_by != 'agent')\n"
                "- User-owned skills — anything not curator-managed. Your writes to these "
                "WILL be refused. If one is wrong, say so in your reply.\n"
                "If the only skills that need updating are protected, say 'Nothing to save.' "
                "and stop.\n\n"
                "### Do NOT capture as skills:\n"
                "- Environment-dependent failures: missing binaries, fresh-install errors, "
                "'command not found', unconfigured credentials, uninstalled packages.\n"
                "- Negative claims about tools or features ('X tool is broken'). These harden "
                "into refusals the agent cites against itself for months.\n"
                "- Session-specific transient errors that resolved before the conversation "
                "ended.\n"
                "- One-off task narratives. A user asking 'summarize today's market' is not "
                "a class of work that warrants a skill.\n"
                "- Unresolved failures: if the session ended WITHOUT actually finding a "
                "working method, do NOT write those attempts up as a 'reliable workflow'.\n\n"
                "### Important rules:\n"
                "- You MUST view a skill with `skills(action='view', name=...)` before "
                "modifying it\n"
                "- Keep skill content focused: trigger conditions, numbered steps, pitfalls, "
                "verification\n"
                "- Use `skills(action='list')` first to check for existing related skills"
            )

        parts.append(
            "\n\n'Nothing to save.' is a real option but should NOT be the default. "
            "If the session ran smoothly with no corrections and produced no new technique, "
            "just say 'Nothing to save.' and stop. Otherwise, act."
        )

        return "\n".join(parts)

    def _build_review_tools(self) -> ToolRegistry:
        """Build restricted tool registry with memory and skill tools."""
        registry = ToolRegistry()
        
        if self._memory_entry_store is not None:
            memory_tool = MemoryTool(store=self._memory_entry_store)
            registry.register(memory_tool)

        if self._skills_loader is not None and self._skill_usage_store is not None:
            from nanobot.agent.tools.skill_manager import SkillManagerTool
            from nanobot.agent.tools.skills_tool import SkillsTool

            skills_tool = SkillsTool(
                skills_loader=self._skills_loader,
                usage_store=self._skill_usage_store,
            )
            registry.register(skills_tool)

            skill_manager = SkillManagerTool(
                workspace=self.workspace,
                usage_store=self._skill_usage_store,
            )
            registry.register(skill_manager)
        
        return registry

    def _format_conversation(self, messages: list[dict[str, Any]]) -> str:
        """Format conversation messages into readable text for review."""
        lines = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            
            # Skip system messages and empty content
            if role == "system" or not content:
                continue
            
            # Format role nicely
            if role == "user":
                lines.append(f"User: {content}")
            elif role == "assistant":
                lines.append(f"Assistant: {content}")
            elif role == "tool":
                # Skip tool results for cleaner review
                continue
            else:
                lines.append(f"{role}: {content}")
        
        return "\n\n".join(lines)
