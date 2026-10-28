from app.stage2c.prompts import INTERPRET, SELECT


def test_interpret_prompt_requires_auditable_segments_and_deterministic_statuses():
    assert 'semantic_segments' in INTERPRET
    assert 'hard' in INTERPRET and 'soft' in INTERPRET and 'unknown' in INTERPRET
    assert 'unresolved' in INTERPRET and 'unsupported' in INTERPRET
    assert 'active_constraints' in INTERPRET
    assert 'explicit hard constraint' in INTERPRET.lower()


def test_prompts_request_english_replies_without_translating_input_evidence():
    assert 'Always write user-facing acknowledgement, questions and explanatory notes in English' in INTERPRET
    assert "regardless of the user's input language" in INTERPRET
    assert 'Keep exact supporting quotes in their original language' in INTERPRET
    assert 'Always write each reason in English' in SELECT
