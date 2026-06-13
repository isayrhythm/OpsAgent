from backend.app.llm.prompts import RESULT_EVALUATOR_SYSTEM_PROMPT


def test_result_evaluator_prompt_declares_output_schema() -> None:
    prompt = RESULT_EVALUATOR_SYSTEM_PROMPT

    assert "JSON schema" in prompt
    assert '"category"' in prompt
    assert '"answered"' in prompt
    assert '"reason"' in prompt
    assert '"missing"' in prompt
    assert '"retry_instruction"' in prompt
    assert "answer|partial|not_found|need_user_input|retry_code" in prompt
