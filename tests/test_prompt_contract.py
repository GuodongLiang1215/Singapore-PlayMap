from app.stage2c.prompts import INTERPRET


def test_interpret_prompt_requires_auditable_segments_and_deterministic_statuses():
    assert 'semantic_segments' in INTERPRET
    assert 'hard' in INTERPRET and 'soft' in INTERPRET and 'unknown' in INTERPRET
    assert 'unresolved' in INTERPRET and 'unsupported' in INTERPRET
    assert 'active_constraints' in INTERPRET
    assert 'explicit hard constraint' in INTERPRET.lower()
