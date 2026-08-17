"""Tests for nanobot.agent.learn_prompt — /learn command prompt builder."""

from __future__ import annotations

import pytest

from nanobot.agent.learn_prompt import build_learn_prompt


class TestBuildLearnPrompt:
    def test_contains_user_request(self):
        prompt = build_learn_prompt("how to use Docker", "standards here")
        assert "how to use Docker" in prompt
        assert "<request>" in prompt
        assert "</request>" in prompt

    def test_contains_skill_creator_standards(self):
        standards = "# Skill Standards\n- Be concise\n- Use examples"
        prompt = build_learn_prompt("topic", standards)
        assert standards in prompt

    def test_contains_research_instructions(self):
        prompt = build_learn_prompt("topic", "standards")
        assert "Gather sources" in prompt
        assert "read_file" in prompt or "search" in prompt

    def test_contains_skill_creation_instruction(self):
        prompt = build_learn_prompt("topic", "standards")
        assert "skill_manage" in prompt
        assert "create" in prompt

    def test_contains_authoring_guidance(self):
        prompt = build_learn_prompt("topic", "standards")
        assert "reusable" in prompt.lower()
        assert "trigger" in prompt.lower()

    def test_empty_request_still_builds(self):
        prompt = build_learn_prompt("", "standards")
        assert "<request></request>" in prompt

    def test_empty_standards_still_builds(self):
        prompt = build_learn_prompt("topic", "")
        assert "topic" in prompt
