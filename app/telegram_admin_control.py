"""Verified group-admin commands for pausing and resuming bot replies."""

from __future__ import annotations

import re

from app.telegram_moderation import api_call


CONTROL_RE = re.compile(
    r"(?iu)^\s*(?:(?:бот|ботик|секретарь|@vbdsThirdSon_bot)\s*[,!:]?\s*)?"
    r"(замолчи|молчи|не\s+говори|не\s+общайся|отключись|говори|можешь\s+говорить|включись)"
    r"\s*[.!?]*\s*$"
)


def parse_control_command(text: str, command: dict[str, str] | None,
                          bot_usernames: set[str]) -> str | None:
    if command:
        target = command.get("target_username") or ""
        if target and target not in bot_usernames:
            return None
        name = command.get("command") or ""
        if name in {"quiet", "silent"}:
            return "quiet"
        if name in {"speak", "unquiet"}:
            return "speak"
        if name == "admins":
            return "admins"
        return None
    match = CONTROL_RE.fullmatch(text)
    if not match:
        return None
    return "speak" if match.group(1).lower() in {"говори", "можешь говорить", "включись"} else "quiet"


def fetch_administrators(chat_id: object, token: str) -> list[dict[str, str]]:
    members = api_call(token, "getChatAdministrators", {"chat_id": chat_id})
    admins = []
    for member in members:
        user = member.get("user") or {}
        user_id = str(user.get("id") or "")
        if member.get("status") in {"administrator", "creator"} and user_id.isdecimal():
            admins.append({"id": user_id, "username": str(user.get("username") or ""),
                           "name": str(user.get("first_name") or "")})
    return admins


def is_current_administrator(chat_id: object, user_id: object, token: str) -> bool:
    value = str(user_id)
    if not value.isdecimal():
        return False
    member = api_call(token, "getChatMember", {"chat_id": chat_id, "user_id": int(value)})
    return member.get("status") in {"administrator", "creator"} and str(
        (member.get("user") or {}).get("id") or "") == value
