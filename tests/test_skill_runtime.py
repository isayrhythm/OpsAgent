import pytest

from backend.app.services.skill_runtime import SkillContractError, _validate_contract


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
