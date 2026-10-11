"""Bounded Telegram moderation; Telegram itself expires temporary restrictions."""
import json
import time
from urllib.request import Request, urlopen

_rights_cache = {}


def api_call(token, method, payload):
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=15) as response:
        result = json.load(response)
    if not result.get("ok"):
        raise RuntimeError("Telegram rejected moderation request")
    return result["result"]


def execute_mute(chat_id, chat_type, action, token, lookup_username=None):
    def failed(reason):
        return {"success": False, "text": f"Мут не выдан: {reason}."}

    if chat_type != "supergroup":
        return failed("эта функция доступна только в супергруппах")
    if not isinstance(action, dict):
        return failed("некорректные параметры")
    target = str(action.get("user_id") or "")
    username = str(action.get("username") or "").strip().lstrip("@").lower()
    if target.startswith("@") and not username:
        username, target = target[1:].lower(), ""
    if username:
        known = lookup_username(username) if lookup_username else None
        if not known:
            return failed(f"не знаю ID @{username}; пусть участник сначала напишет боту")
        resolved = str(known.get("telegram_user_id") or "")
        if target and target != resolved:
            return failed("указанные ID и ник относятся к разным пользователям")
        target = resolved
    minutes = action.get("minutes")
    if not target.isdecimal() or int(target) <= 0:
        return failed("неизвестен числовой ID участника")
    if type(minutes) is not int or not 1 <= minutes <= 10:
        return failed("срок должен быть целым числом от 1 до 10 минут")
    reason = str(action.get("reason") or "").strip()[:300]
    if not reason:
        return failed("не указана причина")
    cache_key = (token, str(chat_id))
    try:
        cached = _rights_cache.get(cache_key)
        if not cached or cached[0] <= time.monotonic():
            bot_id = api_call(token, "getMe", {})["id"]
            rights = api_call(token, "getChatMember", {"chat_id": chat_id, "user_id": bot_id})
            allowed = rights.get("status") == "administrator" and rights.get("can_restrict_members") is True
            cached = (time.monotonic() + 60, bot_id, allowed)
            _rights_cache[cache_key] = cached
        if not cached[2]:
            return failed("у бота нет права ограничивать участников")
        if int(target) == cached[1]:
            return failed("нельзя ограничить самого бота")
        member = api_call(token, "getChatMember", {"chat_id": chat_id, "user_id": int(target)})
        if username and str((member.get("user") or {}).get("username") or "").lower() != username:
            return failed("ник участника изменился; дождитесь его нового сообщения")
        if member.get("status") != "member":
            return failed("участник администратор, отсутствует или уже ограничен")
        until_date = int(time.time()) + minutes * 60
        if api_call(token, "restrictChatMember", {
            "chat_id": chat_id, "user_id": int(target),
            "permissions": {"can_send_messages": False},
            "use_independent_chat_permissions": True,
            "until_date": until_date,
        }) is not True:
            return failed("Telegram не подтвердил ограничение")
    except Exception:
        _rights_cache.pop(cache_key, None)
        return failed("ошибка Telegram API; проверьте права бота")
    user = member.get("user") or {}
    name = "@" + user["username"] if user.get("username") else str(user.get("first_name") or target)
    return {
        "success": True, "target_user_id": target, "minutes": minutes,
        "until_date": until_date, "reason": reason,
        "text": f"Пользователь {name} замучен на {minutes} мин. Причина: {reason}",
    }
