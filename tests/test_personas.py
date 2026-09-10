from app.personas import Persona, build_system_prompt, detect_persona_switch


def test_detects_explicit_alien_switch() -> None:
    assert detect_persona_switch("включи инопланетный голос") == Persona.ALIEN


def test_detects_explicit_ana_switch() -> None:
    assert detect_persona_switch("верни АНА") == Persona.ANA


def test_core_prompt_is_shared_between_personas() -> None:
    ana = build_system_prompt(Persona.ANA)
    alien = build_system_prompt(Persona.ALIEN)

    assert "Priority order" in ana
    assert "Priority order" in alien
    assert "Active persona: ANA" in ana
    assert "Active persona: ALIEN" in alien
