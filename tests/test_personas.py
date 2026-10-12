from app.personas import Persona, build_system_prompt, detect_persona_switch


def test_detects_explicit_alien_switch() -> None:
    assert detect_persona_switch("включи инопланетный голос") == Persona.ALIEN


def test_detects_explicit_ana_switch() -> None:
    assert detect_persona_switch("верни АНА") == Persona.ANA


def test_friend_is_separate_from_ana_and_alien() -> None:
    assert detect_persona_switch("/friend") == Persona.FRIEND
    assert detect_persona_switch("переключись на дружелюбный режим") == Persona.FRIEND
    assert detect_persona_switch("будь дружелюбнее") is None
    friend = build_system_prompt(Persona.FRIEND)
    assert "Активная личность: FRIEND" in friend
    assert "не выдавай себя за человека" in friend


def test_core_prompt_is_shared_between_personas() -> None:
    ana = build_system_prompt(Persona.ANA)
    alien = build_system_prompt(Persona.ALIEN)

    assert "Порядок приоритетов" in ana
    assert "Порядок приоритетов" in alien
    assert "Активная личность: ANA" in ana
    assert "Активная личность: ALIEN" in alien

