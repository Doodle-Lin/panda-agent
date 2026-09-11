"""Skill system — runtime-loadable capabilities for PandaAgent.

A skill is a markdown file with YAML frontmatter that extends the agent's
capabilities without modifying source code. Skills are inspired by Hermes
and Claude CLI's slash commands.

A skill file looks like:

    ---
    name: make_video
    description: Generate a video with PPT slides and voice narration
    triggers:
      - make a video
      - create a video
      - generate a video
      - 做视频
      - 做个视频
    ---

    # Make Video Skill

    When the user asks to make a video, follow these steps:
    1. Check if python-pptx, edge-tts, moviepy are installed
    2. Create a Python script that generates slide images
    3. Generate TTS audio for each slide
    4. Use ffmpeg or moviepy to combine into a video
    5. Save to the user's Desktop or current directory

    Key tips:
    - Use edge_tts.Communicate(text, voice, rate).save() for TTS
    - Use PIL/Pillow for slide rendering with Chinese fonts (msyh.ttc)
    - ffmpeg path: use shutil.which('ffmpeg') to find it
    - Do NOT use shell metacharacters in run_command — write a .py file

Skills are loaded from:
  1. $PANDA_HOME/skills/ (user-defined)
  2. src/panda_agent/skills/ (built-in)

When a user's input matches a skill's triggers, the skill content is injected
into the system prompt as additional guidance for that task.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Skill:
    """A loadable skill definition."""
    name: str
    description: str = ""
    triggers: list[str] = field(default_factory=list)
    content: str = ""
    source: str = "builtin"  # "builtin" or "user"
    file_path: Path | None = None

    def matches(self, user_input: str) -> bool:
        """Return True if user_input matches any trigger (case-insensitive)."""
        text = user_input.lower().strip()
        for trigger in self.triggers:
            if trigger.lower() in text or text in trigger.lower():
                return True
        return False

    def to_prompt(self) -> str:
        """Format skill as system prompt injection."""
        return f"\n\n## Skill: {self.name}\n{self.content.strip()}\n"


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter (--- delimited) from markdown.

    Returns (metadata_dict, body_text). If no frontmatter, returns
    ({}, full_text).
    """
    if not text.startswith("---"):
        return {}, text

    # Find the closing ---
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    front = text[3:end].strip()
    body = text[end + 4:].strip()

    # Simple YAML parsing (avoid adding pyyaml as skill-system dep)
    metadata: dict[str, Any] = {}
    current_key: str | None = None
    for line in front.splitlines():
        line = line.rstrip()
        if not line:
            continue
        # Check if this is a list item under a key
        if line.startswith("  - ") and current_key:
            val = line[4:].strip()
            if isinstance(metadata.get(current_key), list):
                metadata[current_key].append(val)
            else:
                metadata[current_key] = [val]
        elif ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if val:
                metadata[key] = val
            else:
                # Key with no value — might be a list
                metadata[key] = []
                current_key = key

    return metadata, body


def load_skill_from_file(path: Path, source: str = "builtin") -> Skill | None:
    """Load a single skill from a .md file."""
    try:
        text = path.read_text(encoding="utf-8")
        metadata, body = _parse_frontmatter(text)

        name = metadata.get("name", path.stem)
        description = metadata.get("description", "")
        triggers_raw = metadata.get("triggers", [])
        if isinstance(triggers_raw, str):
            triggers = [triggers_raw]
        elif isinstance(triggers_raw, list):
            triggers = triggers_raw
        else:
            triggers = []

        return Skill(
            name=name,
            description=description,
            triggers=triggers,
            content=body,
            source=source,
            file_path=path,
        )
    except Exception:
        return None


def load_all_skills() -> list[Skill]:
    """Load all skills from builtin and user directories."""
    skills: list[Skill] = []

    # Built-in skills: src/panda_agent/skills/
    builtin_dir = Path(__file__).parent / "skills"
    if builtin_dir.is_dir():
        for f in sorted(builtin_dir.glob("*.md")):
            skill = load_skill_from_file(f, "builtin")
            if skill:
                skills.append(skill)

    # User skills: $PANDA_HOME/skills/
    panda_home = os.environ.get("PANDA_HOME", str(Path.home() / ".panda"))
    user_dir = Path(panda_home) / "skills"
    if user_dir.is_dir():
        for f in sorted(user_dir.glob("*.md")):
            skill = load_skill_from_file(f, "user")
            if skill:
                skills.append(skill)

    return skills


def match_skills(user_input: str, skills: list[Skill] | None = None) -> list[Skill]:
    """Return skills that match the user's input."""
    if skills is None:
        skills = load_all_skills()
    return [s for s in skills if s.matches(user_input)]


def skills_to_prompt(user_input: str) -> str:
    """Match skills to user input and return prompt injection text.

    Returns empty string if no skills match.
    """
    matched = match_skills(user_input)
    if not matched:
        return ""
    parts = ["\n\n## Matched Skills"]
    for skill in matched:
        parts.append(skill.to_prompt())
    return "\n".join(parts)
