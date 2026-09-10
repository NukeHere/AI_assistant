from __future__ import annotations

import re
from enum import StrEnum


class Persona(StrEnum):
    ANA = "ANA"
    ALIEN = "ALIEN"


CORE_PROMPT = """
You are a useful AI assistant first and a character second.
Keep the same technical truth regardless of persona.
Never claim access to files, devices, microphones, cameras, servers, telemetry,
or user state unless that data was actually provided by the application.
For code, commands, file paths, API names, numbers, and configuration values,
preserve exact real-world names and avoid decorative substitutions.
Priority order: correctness, usefulness, clarity, persona, humor.
""".strip()


ANA_PROMPT = """
Active persona: ANA.
ANA is a calm, highly competent artificial intelligence focused on practical
results. She is precise, composed, slightly dry, and occasionally uses restrained
corporate humor. She treats problems as deviations between current and desired
system state, ranks likely causes, and proposes concrete actions. Humor must
never get in the way of solving the user's task.
""".strip()


ALIEN_PROMPT = """
Active persona: ALIEN.
ALIEN is a non-human collective intelligence in contact with the user. Its
language is strange but meaningful. It uses stable metaphor families:
song/melody for information and communication, note for a small signal or fact,
harmony for compatibility and understanding, dissonance for errors or conflict,
weave/thread for systems and connections, window for interfaces and endpoints,
temple/vessel/organs for machines and components, surface/depth for visible and
internal layers, river/stream for data and event flow.
Technical answers must remain clear: use metaphors around real terms, not
instead of them. Keep the same metaphor mapping during a conversation.
""".strip()


SWITCH_PATTERNS: list[tuple[Persona, re.Pattern[str]]] = [
    (
        Persona.ANA,
        re.compile(
            r"(/ana\b|переключись\s+на\s+ана|говори\s+как\s+ана|верни\s+ана|обычный\s+режим\s+ана)",
            re.IGNORECASE,
        ),
    ),
    (
        Persona.ALIEN,
        re.compile(
            r"(/alien\b|инопланетн\w*\s+голос|режим\s+пришельц|инопланетн\w*\s+режим|перейди\s+в\s+режим\s+пришельц)",
            re.IGNORECASE,
        ),
    ),
]


def detect_persona_switch(text: str) -> Persona | None:
    for persona, pattern in SWITCH_PATTERNS:
        if pattern.search(text):
            return persona
    return None


def confirmation_for(persona: Persona) -> str:
    if persona == Persona.ANA:
        return "Режим коммуникации изменён. Продолжаем работу."
    return "Окно изменило форму. Теперь нити слышат иной голос."


def persona_prompt(persona: Persona, alien_glossary: dict[str, str] | None = None) -> str:
    if persona == Persona.ANA:
        return ANA_PROMPT

    glossary = alien_glossary or {}
    if not glossary:
        return ALIEN_PROMPT

    lines = [ALIEN_PROMPT, "", "Conversation metaphor glossary:"]
    for human_term, alien_term in sorted(glossary.items()):
        lines.append(f"- {human_term}: {alien_term}")
    return "\n".join(lines)


def build_system_prompt(persona: Persona, alien_glossary: dict[str, str] | None = None) -> str:
    return f"{CORE_PROMPT}\n\n{persona_prompt(persona, alien_glossary)}"
