"""Skill system — runtime-loadable capabilities for PandaAgent.

A skill is a markdown file with YAML frontmatter that extends the agent's
capabilities without modifying source code. Skills are inspired by Hermes
and Claude CLI's slash commands.

Skills are loaded from:
  1. $PANDA_HOME/skills/ (user-defined, auto-generated)
  2. src/panda_agent/skills/ (built-in)

When a user's input matches a skill's triggers, the skill content is injected
into the system prompt as additional guidance for that task.

## Auto-evolution (borrowed from productivity-agent skill-evolution system)

After completing a complex task (5+ tool calls), the agent is instructed to
generate a SKILL.md summarizing what it learned. This skill is stored in
$PANDA_HOME/skills/_auto/ and will be auto-loaded on future matching tasks.

When the agent encounters a problem using an existing skill, it is instructed
to patch it (FIX/VARIANT/ADD) via write_file.

This creates the "越用越聪明" closed loop:
  task → execute → learn → generate/patch skill → future task benefits
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
    source: str = "builtin"  # "builtin", "user", or "auto"
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


def _auto_skills_dir() -> Path:
    """Directory for auto-generated skills."""
    panda_home = os.environ.get("PANDA_HOME", str(Path.home() / ".panda"))
    d = Path(panda_home) / "skills"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_all_skills() -> list[Skill]:
    """Load all skills from builtin, user, and auto directories."""
    skills: list[Skill] = []

    # Built-in skills: src/panda_agent/skills/
    builtin_dir = Path(__file__).parent / "skills"
    if builtin_dir.is_dir():
        for f in sorted(builtin_dir.glob("*.md")):
            skill = load_skill_from_file(f, "builtin")
            if skill:
                skills.append(skill)

    # User + auto skills: $PANDA_HOME/skills/
    user_dir = _auto_skills_dir()
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


# ---------------------------------------------------------------------------
# Skill auto-generation prompt (injected into system prompt)
# ---------------------------------------------------------------------------

SKILL_GENERATION_PROMPT = """

## Skill 自动生成与修补（完成任务后必须执行）

当你在一次任务中调用了 5 次或更多工具，并且任务成功完成时，你必须生成一个 skill 文件来记录经验：

### 生成 Skill 的步骤：
1. 总结任务类型、关键步骤、遇到的坑、解决方案
2. 用 write_file 写入 $PANDA_HOME/skills/ 目录（或 ~/.panda/skills/）
3. 文件名格式：{task_type}_skill.md
4. 文件内容格式：

```markdown
---
name: {skill_name}
description: {一句话描述这个 skill 做什么}
triggers:
  - {触发关键词1}
  - {触发关键词2}
  - {中文触发词}
---

# {Skill 名称}

## 步骤
1. {具体步骤1}
2. {具体步骤2}

## 关键注意事项
- {踩过的坑1}
- {解决方案1}

## 禁止事项
- {不能做的事1}
```

### Skill 修补（使用已有 skill 遇到问题时）：
当你在使用已有 skill 的过程中遇到工具失败、步骤过时、或用户提出不同做法：
- FIX（修复）：步骤/命令/路径有错误 → 读取 SKILL.md，修改后用 write_file 覆盖
- VARIANT（变体）：用户有不同做法 → 在 ## Variations 段追加，不覆盖原有内容
- ADD（补充）：缺少步骤 → 在对应位置插入，用 write_file 覆盖

### SKIP（不满足条件时）：
如果任务很简单（少于5次工具调用）、或是对话/问候、或者已经完成了同类型的任务且无新经验：
不输出任何 skill 相关信息，直接正常结束回复即可。
"""


def get_skill_evolution_prompt() -> str:
    """Return the skill auto-generation prompt to inject into system prompt.

    This is the 'first layer' of the two-layer design from the
    productivity-agent skill-evolution system: the agent generates
    and patches skills itself during conversation, driven by this prompt.
    """
    return SKILL_GENERATION_PROMPT

