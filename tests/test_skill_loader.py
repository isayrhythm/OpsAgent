from pathlib import Path

from backend.app.services.skill_loader import load_skill, load_skill_catalog


def test_load_skill_catalog_reads_frontmatter_without_body(tmp_path: Path) -> None:
    skill_file = tmp_path / "demo.md"
    skill_file.write_text(
        """---
name: demo_skill
version: 2
description: Demo skill
trigger: demo trigger
execution_mode: generated_python
data_paths: data/a.csv, data/b.csv
---

# Body
""",
        encoding="utf-8",
    )

    skills = load_skill_catalog(tmp_path)

    assert len(skills) == 1
    assert skills[0].name == "demo_skill"
    assert skills[0].version == "2"
    assert skills[0].trigger == "demo trigger"
    assert skills[0].data_paths == ["data/a.csv", "data/b.csv"]
    assert skills[0].content == ""


def test_load_skill_includes_content(tmp_path: Path) -> None:
    skill_file = tmp_path / "demo.md"
    skill_file.write_text(
        """---
name: demo_skill
description: Demo skill
---

Use this body.
""",
        encoding="utf-8",
    )

    skill = load_skill(skill_file)

    assert skill.name == "demo_skill"
    assert "Use this body." in skill.content
