import asyncio
from pathlib import Path

import pytest

from backend.app.schemas import ChatHistoryMessage
from backend.app.services.skill_loader import SkillSpec
from backend.app.services.skill_runtime import (
    SkillContractError,
    SkillExecutionContext,
    execute_registered_skill,
    _validate_contract,
)
from backend.app.services.result_evaluator import compact_value


def test_skill_contract_validation_checks_required_arguments() -> None:
    with pytest.raises(SkillContractError, match="threshold is required"):
        _validate_contract(
            {"query": "HY2"},
            {
                "type": "object",
                "required": ["query", "threshold"],
                "properties": {
                    "query": {"type": "string"},
                    "threshold": {"type": "number"},
                },
            },
            "demo input",
        )


def test_skill_contract_validation_accepts_error_output_variant() -> None:
    _validate_contract(
        {"error": "not ready"},
        {
            "anyOf": [
                {"type": "object", "required": ["error"]},
                {"type": "object", "required": ["status", "matches"]},
            ]
        },
        "demo output",
    )


def test_compact_value_preserves_nested_small_records() -> None:
    value = {
        "skill_output": {
            "result": {
                "matches": [
                    {
                        "records": [
                            {
                                "gene_id": "LOC_Os04g54860",
                                "target_sequence": "GCAGTGGATGCAGGCTGATACGG",
                                "validation": "纯合突变，G缺失",
                            }
                        ]
                    }
                ]
            }
        }
    }

    compacted = compact_value(value)

    assert compacted["skill_output"]["result"]["matches"][0]["records"][0]["gene_id"] == "LOC_Os04g54860"
    assert compacted["skill_output"]["result"]["matches"][0]["records"][0]["validation"] == "纯合突变，G缺失"


def test_compact_value_truncates_long_strings_only_when_needed() -> None:
    compacted = compact_value({"text": "x" * 10050})

    assert compacted["text"].startswith("x" * 10000)
    assert "<truncated 50 chars>" in compacted["text"]


def test_registered_message_resolver_includes_recent_history_window(monkeypatch) -> None:
    captured = {}

    def fake_run_gene_info_query(message: str) -> dict:
        captured["message"] = message
        return {"status": "completed", "matches": []}

    monkeypatch.setattr("backend.app.services.skill_runtime.run_gene_info_query", fake_run_gene_info_query)
    skill = SkillSpec(
        name="query_gene_info",
        description="query gene info",
        version="1",
        trigger="query gene info",
        execution_mode="deterministic_python",
        data_paths=[],
        path=Path("skill/gene_info.md"),
        content="",
        executor="query_gene_info",
        argument_resolver="message",
    )

    asyncio.run(
        execute_registered_skill(
            skill,
            SkillExecutionContext(
                message="这个基因具体信息呢？",
                history=[
                    ChatHistoryMessage(role="user", content="帮我看 LOC_Os07g48050 可能跟什么性状有关"),
                    ChatHistoryMessage(role="assistant", content="上一轮返回了该基因的性状预测。"),
                    ChatHistoryMessage(role="user", content="继续"),
                ],
                attachments=[],
                data_profiles=[],
                llm=None,
            ),
        )
    )

    assert "LOC_Os07g48050" in captured["message"]
    assert "最近上下文（最多 8 条" in captured["message"]
