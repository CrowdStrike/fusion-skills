"""Every SKILL.md link must resolve inside its own skill directory.

Assistants other than Claude Code install each skill as its own package, and
awesome-copilot's valid-refs check rejects links that leave the skill directory
(outside-skill-dir) or point at files that don't exist. Name a sibling skill in
prose instead of linking into it.
"""

import os
import re

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SKILLS = os.path.join(_ROOT, "skills")
_LINK = re.compile(r"\]\(([^)\s]+)\)")


def _skill_links():
    """Yield (location, target, resolved path, skill dir) for each relative SKILL.md link."""
    for skill in sorted(os.listdir(_SKILLS)):
        skill_dir = os.path.join(_SKILLS, skill)
        path = os.path.join(skill_dir, "SKILL.md")
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                for target in _LINK.findall(line):
                    if re.match(r"[a-z]+:|#", target):
                        continue
                    resolved = os.path.normpath(
                        os.path.join(skill_dir, target.split("#")[0].split("?")[0])
                    )
                    yield f"skills/{skill}/SKILL.md:{lineno}", target, resolved, skill_dir


def test_no_links_outside_skill_dir():
    """No SKILL.md link may resolve outside its own skill directory."""
    offenders = [
        f"{loc} -> {target}"
        for loc, target, resolved, skill_dir in _skill_links()
        if os.path.commonpath([resolved, skill_dir]) != skill_dir
    ]
    assert not offenders, f"links leave the skill directory: {offenders}"


def test_link_targets_exist():
    """Every SKILL.md link must point at a file or directory that exists."""
    offenders = [
        f"{loc} -> {target}"
        for loc, target, resolved, _ in _skill_links()
        if not os.path.exists(resolved)
    ]
    assert not offenders, f"links point at missing files: {offenders}"
