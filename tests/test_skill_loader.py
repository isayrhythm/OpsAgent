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
answer_requirements: Show a table.; Do not invent data.
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
    assert skills[0].answer_requirements == ["Show a table.", "Do not invent data."]
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


def test_load_skill_reads_executor_and_json_contracts(tmp_path: Path) -> None:
    input_schema = tmp_path / "input.json"
    output_schema = tmp_path / "output.json"
    input_schema.write_text('{"type":"object","required":["query"]}', encoding="utf-8")
    output_schema.write_text('{"type":"object","required":["matches"]}', encoding="utf-8")
    skill_file = tmp_path / "demo.md"
    skill_file.write_text(
        f"""---
name: deterministic_demo
description: Demo contract
execution_mode: deterministic_python
executor: demo_executor
argument_resolver: message
input_schema: {input_schema}
output_schema: {output_schema}
---

Use this contract.
""",
        encoding="utf-8",
    )

    skill = load_skill(skill_file)

    assert skill.executor == "demo_executor"
    assert skill.argument_resolver == "message"
    assert skill.input_schema_path == str(input_schema)
    assert skill.input_schema == {"type": "object", "required": ["query"]}
    assert skill.output_schema == {"type": "object", "required": ["matches"]}
