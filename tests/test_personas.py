from app.personas import Persona, build_system_prompt, detect_persona_switch


def test_detects_explicit_alien_switch() -> None:
    assert detect_persona_switch("включи инопланетный голос") == Persona.ALIEN


def test_detects_explicit_ana_switch() -> None:
    assert detect_persona_switch("верни АНА") == Persona.ANA


def test_core_prompt_is_shared_between_personas() -> None:
    ana = build_system_prompt(Persona.ANA)
    alien = build_system_prompt(Persona.ALIEN)

    assert "Порядок приоритетов" in ana
    assert "Порядок приоритетов" in alien
    assert "Активная личность: ANA" in ana
    assert "Активная личность: ALIEN" in alien

