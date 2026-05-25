import pytest

from backend.app.services.skill_runtime import SkillContractError, _validate_contract
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
