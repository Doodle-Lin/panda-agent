"""Audit Phase 4 skill system tests.

Covers audit findings #11 (matching too loose / YAML parser fragile) and
#12 (skill auto-generation has no post-task verification) from the
2026-09-14 audit.
"""
from __future__ import annotations

from panda_agent.skill_system import (
    Skill,
    load_skill_from_file,
)


# ---------------------------------------------------------------------------
# #11a: matching scoring — short trigger should not match long text
# ---------------------------------------------------------------------------

class TestMatchScoring:
    """A 3-char trigger must not match a 200-char task via bidirectional
    substring; a real substring match must still be detected."""

    def test_short_trigger_does_not_match_long_text(self):
        """Pre-fix: trigger='做视频' (3 chars) matched any text containing it
        AND any text that was a substring of '做视频'. A 200-char task
        description would match via the reverse direction even when the
        trigger string was not in the task."""
        skill = Skill(name="short", triggers=["做视频"])
        long_text = "请帮我整理一下今天的工作汇报文档,把项目进度、风险、下周计划都列清楚," * 5
        assert "做视频" not in long_text
        assert skill.matches(long_text) is False, (
            "short trigger matched a long text via reverse substring — the "
            "bidirectional match made the matching almost arbitrary."
        )

    def test_trigger_present_in_text_matches(self):
        # trigger '做视频' is literally a substring of '帮我做视频要带配音'
        # (note: NOT '帮我做个视频', because that inserts 个 between 做 and 视频)
        skill = Skill(name="video", triggers=["做视频"])
        assert skill.matches("帮我做视频要带配音") is True

    def test_short_input_matching_long_trigger_does_not_match_by_default(self):
        long_trigger = "make a video with ppt slides and voice narration"
        skill = Skill(name="long", triggers=[long_trigger])
        assert skill.matches("make a video") is False, (
            "short input matched a long trigger via reverse substring; the "
            "long trigger is a specific pattern, not a generic prefix."
        )

    def test_exact_match_still_works(self):
        skill = Skill(name="t", triggers=["hello"])
        assert skill.matches("hello") is True
        assert skill.matches("hello world") is True


# ---------------------------------------------------------------------------
# #11b: YAML frontmatter — CRLF, quoted values, parse failures logged
# ---------------------------------------------------------------------------

class TestFrontmatterParser:
    def test_crlf_frontmatter_parses(self, tmp_path):
        crlf_text = (
            "---\r\n"
            "name: crlf_skill\r\n"
            "description: A skill with CRLF endings\r\n"
            "triggers:\r\n"
            "  - make a video\r\n"
            "  - 做个视频\r\n"
            "---\r\n"
            "\r\n"
            "# Body\r\n"
            "Do the thing.\r\n"
        )
        path = tmp_path / "crlf_skill.md"
        path.write_bytes(crlf_text.encode("utf-8"))
        skill = load_skill_from_file(path, "user")
        assert skill is not None, "CRLF frontmatter failed to parse"
        assert skill.name == "crlf_skill"
        assert "make a video" in skill.triggers
        assert "做个视频" in skill.triggers
        assert "Do the thing." in skill.content

    def test_quoted_value_parses(self, tmp_path):
        text = (
            "---\n"
            "name: q\n"
            "description: \"A skill: with colon\"\n"
            "triggers:\n"
            "  - 'single quoted'\n"
            "---\n"
            "Body.\n"
        )
        path = tmp_path / "q.md"
        path.write_text(text, encoding="utf-8")
        skill = load_skill_from_file(path, "user")
        assert skill is not None
        assert skill.description == "A skill: with colon"
        assert "single quoted" in skill.triggers

    def test_parse_failure_logged_to_stderr(self, tmp_path, capsys):
        text = "---\nname: broken\n  bad: : :\n---\nbody\n"
        path = tmp_path / "broken.md"
        path.write_text(text, encoding="utf-8")
        skill = load_skill_from_file(path, "user")
        captured = capsys.readouterr()
        # The implementation may still return None for fatal parse errors,
        # but it must log a stderr message saying so (not silent None).
        if skill is None:
            assert (
                "skill" in captured.err.lower() or "parse" in captured.err.lower()
            ), (
                f"Skill parse failure was silent; expected a stderr log. "
                f"captured.err={captured.err!r}"
            )


# ---------------------------------------------------------------------------
# #12: post-task auto-generation detection
# ---------------------------------------------------------------------------

class TestAutoGenerationDetection:
    def test_new_skill_detected_after_task(self, tmp_path, monkeypatch):
        from panda_agent.skill_system import (
            snapshot_skill_files,
            detect_new_skills_after_task,
        )

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        monkeypatch.setenv("PANDA_HOME", str(tmp_path))

        before = snapshot_skill_files()
        assert before == []

        (skills_dir / "new_skill.md").write_text(
            "---\nname: new_skill\ntriggers:\n  - new\n---\nbody\n",
            encoding="utf-8",
        )

        new_skills = detect_new_skills_after_task(before)
        assert len(new_skills) == 1
        assert new_skills[0].name == "new_skill"

    def test_no_new_skill_when_unchanged(self, tmp_path, monkeypatch):
        from panda_agent.skill_system import (
            snapshot_skill_files,
            detect_new_skills_after_task,
        )

        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "existing.md").write_text(
            "---\nname: existing\ntriggers:\n  - x\n---\nbody\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("PANDA_HOME", str(tmp_path))

        before = snapshot_skill_files()
        new_skills = detect_new_skills_after_task(before)
        assert new_skills == []
