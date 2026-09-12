from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.capabilities import CAPABILITIES_PROMPT, capabilities_answer
from app.personas import (
    Persona,
    build_system_prompt,
    confirmation_for,
    detect_persona_switch,
    extract_alien_glossary_terms,
)
from app.message_blocks import parse_render_blocks
from app.smart_memory import (
    MEMORY_DIRECTIVE_PROMPT,
    build_memory_context,
    build_timed_memory_context,
    extract_memory_directive,
    sanitize_memory_items,
    sanitize_timed_memory_items,
)
from app.storage import Storage

HOST = os.getenv("ASSISTANT_HOST") or ("0.0.0.0" if os.getenv("PORT") else "127.0.0.1")
PORT = int(os.getenv("ASSISTANT_PORT") or os.getenv("PORT") or "8000")
API_TOKEN = os.getenv("APP_API_TOKEN", "dev-token")
MODEL_PROVIDER = os.getenv("MODEL_PROVIDER", "mock").lower()
MODEL_API_BASE_URL = os.getenv("MODEL_API_BASE_URL", "https://api.openai.com/v1")
OLLAMA_API_BASE_URL = os.getenv("OLLAMA_API_BASE_URL", "http://127.0.0.1:11434")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "")
MODEL_API_KEY = os.getenv("MODEL_API_KEY", "")
MODEL_NAME = os.getenv("MODEL_NAME", "gpt-4o-mini")
DATABASE_PATH = Path(os.getenv("DATABASE_PATH", "./data/assistant.sqlite3"))
CLIENT_HISTORY_LIMIT = int(os.getenv("CLIENT_HISTORY_LIMIT", "20"))
DEFAULT_CLIENT_ID = os.getenv("DEFAULT_CLIENT_ID", "primary-user")
DEFAULT_CONVERSATION_ID = os.getenv("DEFAULT_CONVERSATION_ID", "default")
ASSISTANT_TIMEZONE = os.getenv("ASSISTANT_TIMEZONE", "Europe/Moscow")
TG_BOT_API_KEY = os.getenv("TG_BOT_API_KEY", "")
TG_WEBHOOK_SECRET = os.getenv("TG_WEBHOOK_SECRET", "")
TG_BOT_USERNAME = os.getenv("TG_BOT_USERNAME", "VBDsThirdSon_bot").lstrip("@")
TG_GUEST_CHAT_MODE = os.getenv("TG_GUEST_CHAT_MODE", "true").lower() not in {"0", "false", "no", "off"}
TG_SECRETARY_MODE = os.getenv("TG_SECRETARY_MODE", "true").lower() not in {"0", "false", "no", "off"}
TG_BOT_TO_BOT = os.getenv("TG_BOT_TO_BOT", "true").lower() not in {"0", "false", "no", "off"}
AUDIT_LOG_PATH = Path(os.getenv("ASSISTANT_AUDIT_LOG_PATH", "./data/assistant-audit.jsonl"))
AUDIT_LOG_MAX_TEXT = int(os.getenv("ASSISTANT_AUDIT_LOG_MAX_TEXT", "1200"))

storage = Storage(DATABASE_PATH)


def text_preview(text: object, limit: int | None = None) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    max_length = limit or AUDIT_LOG_MAX_TEXT
    if len(value) <= max_length:
        return value
    return value[:max_length] + "…"


def audit_event(event: str, **fields: object) -> None:
    safe_fields = {
        key: text_preview(value) if key.endswith("_text") or key.endswith("_preview") else value
        for key, value in fields.items()
        if "token" not in key.lower() and "secret" not in key.lower() and "key" not in key.lower()
    }
    record = {
        "time_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "event": event,
        **safe_fields,
    }
    try:
        AUDIT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG_PATH.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
    print("AUDIT " + json.dumps(record, ensure_ascii=False), flush=True)

def current_times() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    now_utc = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    try:
        local = now.astimezone(ZoneInfo(ASSISTANT_TIMEZONE))
    except Exception:
        if ASSISTANT_TIMEZONE == "Europe/Moscow":
            local = now.astimezone(timezone(timedelta(hours=3)))
        else:
            local = now
    now_local = local.isoformat(timespec="seconds")
    return now_utc, now_local

def materialize_due(client_id: str, conversation_id: str) -> list[dict[str, object]]:
    now_utc, _ = current_times()
    return storage.materialize_due_timed_memories(client_id, conversation_id, now_utc)


def complete(messages: list[dict[str, str]]) -> tuple[str, str]:
    if MODEL_PROVIDER == "ollama":
        return complete_ollama(messages), "ollama"
    if MODEL_PROVIDER == "openai" or MODEL_API_KEY:
        return complete_openai_compatible(messages), "api"
    if MODEL_PROVIDER != "mock":
        raise RuntimeError(f"Unknown MODEL_PROVIDER: {MODEL_PROVIDER}")
    return mock_response(messages), "mock"


def handle_history_request(body: dict[str, object]) -> dict[str, object]:
    client_id = str(body.get("client_id") or DEFAULT_CLIENT_ID).strip()
    conversation_id = str(body.get("conversation_id") or DEFAULT_CONVERSATION_ID).strip()
    limit = int(body.get("limit") or CLIENT_HISTORY_LIMIT)
    conversation = storage.ensure_conversation(client_id, conversation_id)
    return {
        "conversation_id": conversation_id,
        "persona": conversation["persona"],
        "messages": storage.recent_messages(conversation_id, limit),
    }


def response_payload(
    conversation_id: str,
    persona: str,
    text: str,
    **extra: object,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "conversation_id": conversation_id,
        "persona": persona,
        "text": text,
        "render_blocks": parse_render_blocks(persona, text),
    }
    payload.update(extra)
    return payload


def handle_sync_request(body: dict[str, object]) -> dict[str, object]:
    client_id = str(body.get("client_id") or DEFAULT_CLIENT_ID).strip()
    conversation_id = str(body.get("conversation_id") or DEFAULT_CONVERSATION_ID).strip()
    limit = int(body.get("limit") or CLIENT_HISTORY_LIMIT)
    events = body.get("messages") or body.get("events") or []
    if not client_id:
        raise ValueError("client_id is required")
    if not conversation_id:
        raise ValueError("conversation_id is required")
    if not isinstance(events, list):
        raise ValueError("messages/events must be an array")
    result = storage.sync_messages(client_id, conversation_id, events, limit=limit)
    materialize_due(client_id, conversation_id)
    refreshed = storage.sync_messages(client_id, conversation_id, [], limit=limit)
    refreshed["imported"] = result.get("imported", 0)
    return refreshed


def handle_snapshot_request(body: dict[str, object]) -> dict[str, object]:
    client_id = str(body.get("client_id") or DEFAULT_CLIENT_ID).strip()
    action = str(body.get("action") or "export").strip().lower()
    if not client_id:
        raise ValueError("client_id is required")
    if action == "export":
        snapshot = storage.export_client_snapshot(client_id)
        return {"ok": True, "action": "export", "snapshot": snapshot}
    if action == "import":
        snapshot = body.get("snapshot")
        if not isinstance(snapshot, dict):
            raise ValueError("snapshot object is required")
        counts = storage.import_client_snapshot(client_id, snapshot)
        return {"ok": True, "action": "import", "imported": counts}
    raise ValueError("action must be export or import")


def handle_logs_request(body: dict[str, object]) -> dict[str, object]:
    limit = int(body.get("limit") or 100)
    limit = max(1, min(limit, 500))
    if not AUDIT_LOG_PATH.exists():
        return {"ok": True, "logs": [], "path": str(AUDIT_LOG_PATH)}
    lines = AUDIT_LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
    logs: list[object] = []
    for line in lines:
        try:
            logs.append(json.loads(line))
        except json.JSONDecodeError:
            logs.append({"raw": line})
    return {"ok": True, "logs": logs, "path": str(AUDIT_LOG_PATH)}

def handle_memory_command(client_id: str, conversation_id: str, text: str, persona: Persona) -> dict[str, object] | None:
    stripped = text.strip()
    lower = stripped.lower()

    content = ""
    if lower.startswith("/remember"):
        content = stripped[len("/remember") :].strip(" :-")
    elif lower.startswith("запомни:"):
        content = stripped.split(":", 1)[1].strip()
    elif lower.startswith("запомни, что"):
        content = stripped[len("запомни, что") :].strip(" .")
    elif lower.startswith("запомни что"):
        content = stripped[len("запомни что") :].strip(" .")

    if content:
        storage.remember(client_id, "manual", content, importance=4)
        storage.add_message(conversation_id, "user", text)
        if persona == Persona.ALIEN:
            answer = "Нота помещена в глубину памяти. Она будет звучать в следующих песнях."
        else:
            answer = "Запись добавлена в память. Буду учитывать это в следующих ответах."
        storage.add_message(conversation_id, "assistant", answer)
        return response_payload(
            conversation_id,
            persona.value,
            answer,
            switched=False,
            memory_updated=True,
            memory_saved=1,
            memory_recalled=0,
            model_mode=provider_mode(),
        )

    if lower in {"/id", "id", "мой id", "мой app id", "app id"}:
        storage.add_message(conversation_id, "user", text)
        answer = f"Твой app client_id: `{client_id}`\nconversation_id: `{conversation_id}`\nДля привязки Telegram напиши боту: /link {client_id}"
        storage.add_message(conversation_id, "assistant", answer)
        return response_payload(
            conversation_id,
            persona.value,
            answer,
            switched=False,
            memory_updated=False,
            memory_saved=0,
            memory_recalled=0,
            model_mode=provider_mode(),
        )

    if lower in {"/functions", "/capabilities", "функции", "что ты умеешь"} or lower.startswith("что ты можешь"):
        storage.add_message(conversation_id, "user", text)
        answer = capabilities_answer(persona)
        storage.add_message(conversation_id, "assistant", answer)
        return {
            "conversation_id": conversation_id,
            "persona": persona.value,
            "switched": False,
            "memory_updated": False,
            "memory_saved": 0,
            "memory_recalled": 0,
            "text": answer,
            "model_mode": provider_mode(),
        }

    if lower in {"/timers", "/reminders", "таймеры", "напоминания"}:
        storage.add_message(conversation_id, "user", text)
        timers = storage.list_timed_memories(client_id, conversation_id, include_triggered=False, limit=20)
        if not timers:
            answer = "Активных временных напоминаний нет." if persona == Persona.ANA else "В глубине нет отложенных нот."
        else:
            lines = ["Активные временные напоминания:" if persona == Persona.ANA else "Отложенные ноты в глубине:"]
            for index, timer in enumerate(timers, start=1):
                lines.append(f"{index}. {timer['due_at']} — {timer['summary']}")
            answer = "\n".join(lines)
        storage.add_message(conversation_id, "assistant", answer)
        return response_payload(
            conversation_id,
            persona.value,
            answer,
            switched=False,
            memory_updated=False,
            memory_saved=0,
            memory_recalled=0,
            timed_memory_saved=0,
            model_mode=provider_mode(),
        )
    if lower in {"/memory", "/memories", "память"} or lower.startswith("что ты помнишь"):
        storage.add_message(conversation_id, "user", text)
        memories = storage.list_memories(client_id, limit=20)
        if not memories:
            answer = "В памяти пока нет записей." if persona == Persona.ANA else "Глубина пока пуста. Ноты ещё не осели."
        else:
            lines = ["Память:" if persona == Persona.ANA else "Ноты в глубине:"]
            for index, memory in enumerate(memories, start=1):
                lines.append(f"{index}. {memory['summary']}")
            answer = "\n".join(lines)
        storage.add_message(conversation_id, "assistant", answer)
        return response_payload(
            conversation_id,
            persona.value,
            answer,
            switched=False,
            memory_updated=False,
            memory_saved=0,
            memory_recalled=0,
            model_mode=provider_mode(),
        )

    return None


def build_messages_for_model(
    client_id: str,
    conversation_id: str,
    text: str,
    persona: Persona,
    channel: str = "desktop",
    channel_context: str = "",
) -> list[dict[str, str]]:
    alien_glossary = storage.alien_glossary(conversation_id)
    system_prompt = build_system_prompt(persona, alien_glossary)
    system_prompt += "\n\n" + CAPABILITIES_PROMPT
    if channel == "telegram_group":
        system_prompt += "\n\nПравила Telegram-группы: отвечай на русском и особенно компактно. В группе отвечай только когда к тебе обращаются или ответ явно полезен; обычная реплика — 1-2 коротких абзаца или пункта. Избегай структурных секций, если пользователь не просит диагностику или план. Не выводи сырой Markdown, который Telegram плохо отображает."
    elif channel == "telegram":
        system_prompt += "\n\nПравила личного Telegram-чата: отвечай на русском, если пользователь не попросил иначе; обычные ответы держи короткими, обычно 1-4 предложения. Избегай секций **Наблюдение:**, **Диагностика:** и других структурных меток, если пользователь не просит анализ, план или диагностику. Не выводи сырой Markdown, который Telegram плохо отображает."
    if channel_context:
        system_prompt += "\n\nКонтекст текущего канала:\n" + channel_context
    system_prompt += "\n\n" + MEMORY_DIRECTIVE_PROMPT
    now_utc, now_local = current_times()
    due_timers = materialize_due(client_id, conversation_id)
    system_prompt += "\n\n" + build_timed_memory_context(due_timers, now_utc, now_local)

    memory_summaries = storage.memory_summaries_for_prompt(client_id, text)
    memory_context = build_memory_context(memory_summaries)
    if memory_context:
        system_prompt += "\n\n" + memory_context

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(storage.recent_messages(conversation_id, CLIENT_HISTORY_LIMIT))
    messages.append({"role": "user", "content": text})
    return messages


def save_memory_items(client_id: str, items: list[dict[str, object]]) -> int:
    saved = 0
    for item in sanitize_memory_items(items):
        storage.add_memory_cell(
            client_id=client_id,
            kind=item["kind"],
            summary=item["summary"],
            full_content=item["full"],
            topics=item["topics"],
            importance=item["importance"],
        )
        saved += 1
    return saved




def save_timed_memory_items(client_id: str, conversation_id: str, items: list[dict[str, object]]) -> int:
    saved = 0
    for item in sanitize_timed_memory_items(items):
        storage.add_timed_memory(
            client_id=client_id,
            conversation_id=conversation_id,
            summary=item["summary"],
            full_content=item["full"],
            due_at=item["due_at"],
            timezone_name=item["timezone"],
            source="model",
        )
        saved += 1
    return saved

def complete_with_smart_memory(
    client_id: str,
    conversation_id: str,
    messages: list[dict[str, str]],
) -> tuple[str, str, int, int, int, str | None, dict[str, object] | None]:
    raw_answer, model_mode = complete(messages)
    answer, directive = extract_memory_directive(raw_answer)
    saved = save_memory_items(client_id, directive.remember)
    timed_saved = save_timed_memory_items(client_id, conversation_id, directive.timers)
    recalled = 0
    requested_persona = directive.persona
    telegram_action = sanitize_telegram_action(directive.telegram_action)

    if directive.recall:
        full_memories = storage.search_memory_cells(client_id, directive.recall, limit=5)
        recalled = len(full_memories)
        if full_memories:
            recall_context = build_memory_context([], full_memories)
            recall_messages = messages + [
                {"role": "assistant", "content": answer or "Запрошен доступ к памяти."},
                {
                    "role": "system",
                    "content": recall_context + "\n\nОтветь на последнее сообщение пользователя, используя найденную полную память. Если нужно, можешь сохранить новую память.",
                },
            ]
            raw_answer, model_mode = complete(recall_messages)
            answer, second_directive = extract_memory_directive(raw_answer)
            saved += save_memory_items(client_id, second_directive.remember)
            timed_saved += save_timed_memory_items(client_id, conversation_id, second_directive.timers)
            if second_directive.persona:
                requested_persona = second_directive.persona
            second_telegram_action = sanitize_telegram_action(second_directive.telegram_action)
            if second_telegram_action:
                telegram_action = second_telegram_action

    return answer, model_mode, saved, recalled, timed_saved, requested_persona, telegram_action


def handle_message_request(body: dict[str, object]) -> dict[str, object]:
    client_id = str(body.get("client_id") or DEFAULT_CLIENT_ID).strip()
    conversation_id = str(body.get("conversation_id") or DEFAULT_CONVERSATION_ID).strip()
    text = str(body.get("text") or "").strip()
    channel = str(body.get("channel") or body.get("input_type") or "desktop").strip().lower()
    channel_context = str(body.get("channel_context") or "").strip()

    if not client_id:
        raise ValueError("client_id is required")
    if not conversation_id:
        raise ValueError("conversation_id is required")
    if not text:
        raise ValueError("text is required")

    conversation = storage.ensure_conversation(client_id, conversation_id)
    active_persona = Persona(conversation["persona"])
    audit_event("message_received", client_id=client_id, conversation_id=conversation_id, channel=channel, persona=active_persona.value, text_preview=text)

    switched_persona = detect_persona_switch(text)
    if switched_persona is not None:
        storage.set_persona(client_id, conversation_id, switched_persona)
        storage.add_message(conversation_id, "user", text)
        answer = confirmation_for(switched_persona)
        storage.add_message(conversation_id, "assistant", answer)
        payload = response_payload(
            conversation_id,
            switched_persona.value,
            answer,
            switched=True,
            memory_saved=0,
            memory_recalled=0,
            model_mode=provider_mode(),
        )
        audit_event("message_handled", client_id=client_id, conversation_id=conversation_id, channel=channel, persona=switched_persona.value, switched=True, memory_saved=0, memory_recalled=0, timed_memory_saved=0, response_preview=answer)
        return payload

    memory_response = handle_memory_command(client_id, conversation_id, text, active_persona)
    if memory_response is not None:
        audit_event("message_handled", client_id=client_id, conversation_id=conversation_id, channel=channel, persona=memory_response.get("persona"), command="memory", memory_saved=memory_response.get("memory_saved"), memory_recalled=memory_response.get("memory_recalled"), timed_memory_saved=memory_response.get("timed_memory_saved"), response_preview=memory_response.get("text"))
        return memory_response

    messages = build_messages_for_model(client_id, conversation_id, text, active_persona, channel=channel, channel_context=channel_context)

    storage.add_message(conversation_id, "user", text)
    if active_persona == Persona.ALIEN:
        storage.update_alien_glossary(client_id, conversation_id, extract_alien_glossary_terms(text))
    answer, model_mode, saved, recalled, timed_saved, requested_persona, telegram_action = complete_with_smart_memory(client_id, conversation_id, messages)
    if requested_persona in {"ANA", "ALIEN"}:
        active_persona = Persona(requested_persona)
        storage.set_persona(client_id, conversation_id, active_persona)
    storage.add_message(conversation_id, "assistant", answer)

    payload = response_payload(
        conversation_id,
        active_persona.value,
        answer,
        switched=False,
        memory_updated=saved > 0,
        memory_saved=saved,
        memory_recalled=recalled,
        timed_memory_saved=timed_saved,
        model_mode=model_mode,
        telegram_action=telegram_action,
    )
    audit_event("message_handled", client_id=client_id, conversation_id=conversation_id, channel=channel, persona=active_persona.value, model_mode=model_mode, memory_saved=saved, memory_recalled=recalled, timed_memory_saved=timed_saved, telegram_action=bool(telegram_action), response_preview=answer)
    return payload
def handle_telegram_update(body: dict[str, object]) -> dict[str, object]:
    if not TG_BOT_API_KEY:
        audit_event("telegram_ignored", reason="telegram_disabled")
        return {"ok": True, "ignored": True, "reason": "telegram_disabled"}
    message = body.get("message") or body.get("edited_message")
    if not isinstance(message, dict):
        audit_event("telegram_ignored", reason="no_message")
        return {"ok": True, "ignored": True, "reason": "no_message"}
    text = str(message.get("text") or "").strip()
    if not text:
        audit_event("telegram_ignored", reason="no_text")
        return {"ok": True, "ignored": True, "reason": "no_text"}
    chat = message.get("chat") if isinstance(message.get("chat"), dict) else {}
    sender = message.get("from") if isinstance(message.get("from"), dict) else {}
    chat_id = chat.get("id")
    telegram_user_id = str(sender.get("id") or "").strip()
    chat_type = str(chat.get("type") or "private")
    username = str(sender.get("username") or "")
    if chat_id is None or not telegram_user_id:
        audit_event("telegram_ignored", reason="no_chat_or_user", chat_type=chat_type, text_preview=text)
        return {"ok": True, "ignored": True, "reason": "no_chat_or_user"}
    is_bot = bool(sender.get("is_bot"))
    if is_bot and not TG_BOT_TO_BOT:
        audit_event("telegram_ignored", reason="bot_sender", chat_id=chat_id, telegram_user_id=telegram_user_id, username=username, text_preview=text)
        return {"ok": True, "ignored": True, "reason": "bot_sender"}

    addressed = is_telegram_addressed(text)
    user = storage.upsert_telegram_user(
        telegram_user_id=telegram_user_id,
        username=username,
        first_name=str(sender.get("first_name") or ""),
        last_name=str(sender.get("last_name") or ""),
        is_bot=is_bot,
    )
    command_text = normalize_telegram_command(text)
    linked_client_id, is_authorized = storage.telegram_client_context(telegram_user_id)
    audit_event(
        "telegram_received",
        chat_id=chat_id,
        chat_type=chat_type,
        telegram_user_id=telegram_user_id,
        username=username,
        authorized=is_authorized,
        addressed=addressed,
        text_preview=text,
    )

    if should_ignore_telegram_message(command_text, chat_type, is_authorized, addressed=addressed):
        audit_event("telegram_ignored", reason="secretary_mode", chat_id=chat_id, chat_type=chat_type, telegram_user_id=telegram_user_id, authorized=is_authorized, addressed=addressed, text_preview=text)
        return {"ok": True, "ignored": True, "reason": "secretary_mode"}

    if command_text.startswith("/start"):
        reply = telegram_start_text(user, linked_client_id, is_authorized)
        send_telegram_message(chat_id, reply)
        audit_event("telegram_command", command="start", chat_id=chat_id, telegram_user_id=telegram_user_id, response_preview=reply)
        return {"ok": True, "handled": "start"}
    if command_text.startswith("/link"):
        parts = command_text.split(maxsplit=1)
        if len(parts) < 2 or not re.fullmatch(r"[A-Za-z0-9_.-]{3,128}", parts[1].strip()):
            reply = "Пришли app client_id так: /link primary-user. В приложении его можно узнать командой /id."
            send_telegram_message(chat_id, reply)
            audit_event("telegram_command", command="link_help", chat_id=chat_id, telegram_user_id=telegram_user_id, response_preview=reply)
            return {"ok": True, "handled": "link_help"}
        linked = storage.link_telegram_user(telegram_user_id, parts[1].strip())
        migrated = migrate_guest_memories(str(linked.get("guest_client_id") or ""), str(linked.get("app_client_id") or ""))
        reply = f"Готово. Telegram привязан к app client_id: {linked['app_client_id']}. Перенесено guest-memory: {migrated}."
        send_telegram_message(chat_id, reply)
        audit_event("telegram_command", command="linked", chat_id=chat_id, telegram_user_id=telegram_user_id, app_client_id=linked.get("app_client_id"), migrated_memories=migrated, response_preview=reply)
        return {"ok": True, "handled": "linked", "migrated_memories": migrated}
    if command_text.startswith("/unlink"):
        storage.unlink_telegram_user(telegram_user_id)
        reply = "Привязка Telegram отключена. Дальше будет гостевой режим."
        send_telegram_message(chat_id, reply)
        audit_event("telegram_command", command="unlinked", chat_id=chat_id, telegram_user_id=telegram_user_id, response_preview=reply)
        return {"ok": True, "handled": "unlinked"}
    if command_text.startswith("/whoami") or command_text.startswith("/id"):
        app_id = linked_client_id
        state = "authorized" if is_authorized else "guest"
        safe_username = user.get("username") or "-"
        reply = f"Telegram id: {telegram_user_id}\nusername: @{safe_username}\nmode: {state}\napp client_id: {app_id}"
        send_telegram_message(chat_id, reply)
        audit_event("telegram_command", command="whoami", chat_id=chat_id, telegram_user_id=telegram_user_id, app_client_id=app_id, response_preview=reply)
        return {"ok": True, "handled": "whoami"}

    if not is_authorized and not TG_GUEST_CHAT_MODE:
        reply = "Сейчас включён только авторизованный режим. Напиши /link app_client_id."
        send_telegram_message(chat_id, reply)
        audit_event("telegram_ignored", reason="guest_disabled", chat_id=chat_id, telegram_user_id=telegram_user_id, text_preview=text, response_preview=reply)
        return {"ok": True, "handled": "guest_disabled"}

    conversation_id = DEFAULT_CONVERSATION_ID if is_authorized else f"telegram-{telegram_user_id}"
    user_text = command_text
    if not is_authorized:
        user_text = f"[Telegram guest @{user.get('username') or telegram_user_id}] {command_text}"
    payload = handle_message_request({
        "client_id": linked_client_id,
        "conversation_id": conversation_id,
        "text": user_text,
        "channel": "telegram_group" if chat_type != "private" else "telegram",
        "channel_context": telegram_channel_context(user, chat, chat_type, is_authorized),
    })
    route_result = route_telegram_response(chat_id, telegram_user_id, user, chat_type, payload)
    audit_event(
        "telegram_handled",
        chat_id=chat_id,
        chat_type=chat_type,
        telegram_user_id=telegram_user_id,
        username=username,
        authorized=is_authorized,
        addressed=addressed,
        memory_saved=payload.get("memory_saved"),
        memory_recalled=payload.get("memory_recalled"),
        timed_memory_saved=payload.get("timed_memory_saved"),
        telegram_route=route_result.get("telegram_route"),
        text_preview=text,
        response_preview=payload.get("text"),
    )
    return {"ok": True, "handled": "message", "authorized": is_authorized, **route_result}
def normalize_telegram_command(text: str) -> str:
    stripped = text.strip()
    if TG_BOT_USERNAME:
        stripped = re.sub(rf"^(/\w+)@{re.escape(TG_BOT_USERNAME)}\b", r"\1", stripped, flags=re.IGNORECASE)
        stripped = re.sub(rf"@{re.escape(TG_BOT_USERNAME)}\b", "", stripped, flags=re.IGNORECASE).strip()
    return stripped


def should_ignore_telegram_message(text: str, chat_type: str, is_authorized: bool, addressed: bool | None = None) -> bool:
    if not TG_SECRETARY_MODE:
        return False
    if text.startswith("/"):
        return False
    if chat_type == "private":
        return False
    was_addressed = is_telegram_addressed(text) if addressed is None else addressed
    if was_addressed:
        return False
    return True


def is_telegram_addressed(text: str) -> bool:
    lowered = text.lower()
    usernames = [name.lower() for name in [TG_BOT_USERNAME, "VBDsThirdSon_bot"] if name]
    for username in usernames:
        if re.search(rf"(?<![\w@])@{re.escape(username)}(?!\w)", lowered, flags=re.IGNORECASE):
            return True
    patterns = [
        r"(?<!\w)бот(?:ик)?(?!\w)",
        r"(?<!\w)секретарь(?!\w)",
        r"(?<!\w)ана(?!\w)",
        r"(?<!\w)алиен(?!\w)",
        r"(?<!\w)alien(?!\w)",
        r"(?<!\w)bot(?!\w)",
    ]
    return any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in patterns)


def is_telegram_mention(text: str) -> bool:
    return is_telegram_addressed(text)
def sanitize_telegram_action(action: object) -> dict[str, object] | None:
    if not isinstance(action, dict):
        return None
    reply_to = str(action.get("reply_to") or action.get("target") or "group").strip().lower()
    if reply_to not in {"group", "private", "both"}:
        reply_to = "group"
    group_text = str(action.get("group_text") or "").strip()
    private_text = str(action.get("private_text") or "").strip()
    return {
        "reply_to": reply_to,
        "mention_sender": bool(action.get("mention_sender") or action.get("mention_user")),
        "group_text": group_text[:2000],
        "private_text": private_text[:3800],
    }


def telegram_channel_context(user: dict[str, object], chat: dict[str, object], chat_type: str, is_authorized: bool) -> str:
    username = str(user.get("username") or "").strip()
    first_name = str(user.get("first_name") or "").strip()
    last_name = str(user.get("last_name") or "").strip()
    display_name = " ".join(part for part in [first_name, last_name] if part).strip() or username or "unknown"
    mention = f"@{username}" if username else display_name
    chat_title = str(chat.get("title") or chat.get("username") or "").strip()
    scope = "личный чат" if chat_type == "private" else "групповой чат"
    auth = "авторизованный пользователь приложения" if is_authorized else "гостевой пользователь Telegram"
    lines = [
        f"Telegram-область: {scope}.",
        f"Отправитель: {display_name}; упоминание username: {mention}; статус: {auth}.",
    ]
    if chat_title:
        lines.append(f"Название чата: {chat_title}.")
    if chat_type == "private":
        lines.append("Telegram-ответ обычно должен быть кратким: 1-3 коротких абзаца, если пользователь явно не просит подробности.")
    if chat_type != "private":
        lines.append("В группе отвечай особенно компактно: обычно 1-3 короткие строки. Если нужен длинный или личный ответ, используй telegram_action для личного сообщения.")
        lines.append(
            "В этой группе можно приватно запросить assistant_memory telegram_action: "
            "reply_to=group для компактного публичного ответа, reply_to=private для конфиденциальных/персональных данных, "
            "reply_to=both для короткой групповой реплики и более полного личного сообщения. "
            "Используй mention_sender=true, когда обращаешься к отправителю в группе."
        )
    return "\n".join(lines)


def telegram_sender_mention_text(user: dict[str, object]) -> str:
    username = str(user.get("username") or "").strip()
    if username:
        return f"@{username}"
    name = " ".join(str(user.get(part) or "").strip() for part in ["first_name", "last_name"]).strip()
    return name or "пользователь"


def route_telegram_response(chat_id: object, telegram_user_id: str, user: dict[str, object], chat_type: str, payload: dict[str, object]) -> dict[str, object]:
    answer = str(payload.get("text") or "")
    action = sanitize_telegram_action(payload.get("telegram_action")) or {"reply_to": "group", "mention_sender": False, "group_text": "", "private_text": ""}
    if chat_type == "private":
        send_telegram_message(chat_id, str(action.get("private_text") or answer))
        return {"telegram_route": "private"}

    reply_to = str(action.get("reply_to") or "group")
    group_text = str(action.get("group_text") or "").strip()
    private_text = str(action.get("private_text") or "").strip() or answer
    if reply_to in {"group", "both"} and not group_text:
        group_text = answer
    if group_text and bool(action.get("mention_sender")):
        mention = telegram_sender_mention_text(user)
        if mention not in group_text:
            group_text = f"{mention}, {group_text}"

    sent_group = False
    sent_private = False
    if reply_to in {"group", "both"} and group_text:
        send_telegram_message(chat_id, group_text)
        sent_group = True
    if reply_to in {"private", "both"} and private_text:
        sent_private = try_send_telegram_message(telegram_user_id, private_text)
        if not sent_private and not sent_group:
            send_telegram_message(chat_id, f"{telegram_sender_mention_text(user)}, не смог написать в личку. Напиши мне /start в личном чате и повтори запрос.")
            sent_group = True
    return {"telegram_route": reply_to, "telegram_group_sent": sent_group, "telegram_private_sent": sent_private}


def try_send_telegram_message(chat_id: object, text: str) -> bool:
    try:
        send_telegram_message(chat_id, text)
    except (HTTPError, URLError, TimeoutError, OSError, RuntimeError):
        return False
    return True

def telegram_start_text(user: dict[str, object], client_id: str, is_authorized: bool) -> str:
    username = user.get("username") or user.get("first_name") or "гость"
    if is_authorized:
        return f"Привет, {username}. Telegram уже привязан к app client_id: {client_id}. Пиши сюда — я синхронизируюсь с основной памятью."
    return (
        f"Привет, {username}. Я AI Assistant в гостевом Telegram-режиме.\n"
        "Чтобы связать меня с desktop/Android, в приложении напиши /id, затем сюда отправь /link app_client_id.\n"
        f"Пока твой временный guest client_id: {client_id}."
    )


def send_telegram_message(chat_id: object, text: str, persona: str = "") -> None:
    if not TG_BOT_API_KEY:
        return
    clean_text = telegram_message_html(text, persona) or "..."
    payload = json.dumps({"chat_id": chat_id, "text": clean_text[:3800], "parse_mode": "HTML"}, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{TG_BOT_API_KEY}/sendMessage",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=30) as response:
        response.read()



def telegram_message_html(text: str, persona: str = "") -> str:
    blocks = parse_render_blocks(persona if persona in {"ANA", "ALIEN"} else "ANA", text)
    if blocks:
        lines: list[str] = []
        for block in blocks:
            label = str(block.get("label") or "").strip()
            content = strip_basic_markdown(str(block.get("text") or "").strip())
            if label and content:
                lines.append(f"<b>{html.escape(label)}</b>\n{html.escape(content)}")
            elif content:
                lines.append(html.escape(content))
        if lines:
            return "\n\n".join(lines).strip()
    return html.escape(strip_basic_markdown(text).strip())


def strip_basic_markdown(text: str) -> str:
    cleaned = re.sub(r"```(?:\w+)?\s*(.*?)\s*```", r"\1", text, flags=re.DOTALL)
    cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned)
    cleaned = re.sub(r"\*\*([^*]+)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__([^_]+)__", r"\1", cleaned)
    cleaned = re.sub(r"(?m)^\s*[-*]\s+", "- ", cleaned)
    return cleaned.strip()

def migrate_guest_memories(guest_client_id: str, app_client_id: str) -> int:
    if not guest_client_id or not app_client_id or guest_client_id == app_client_id:
        return 0
    memories = storage.list_memories(guest_client_id, limit=20)
    migrated = 0
    for memory in memories:
        storage.add_memory_cell(
            client_id=app_client_id,
            kind="telegram_guest",
            summary=str(memory.get("summary") or memory.get("content") or "")[:240],
            full_content=str(memory.get("full_content") or memory.get("content") or ""),
            topics=["telegram", "guest"],
            importance=min(3, int(memory.get("importance") or 1)),
        )
        migrated += 1
    return migrated


def complete_openai_compatible(messages: list[dict[str, str]]) -> str:
    if not MODEL_API_KEY:
        raise RuntimeError("MODEL_API_KEY is required for openai-compatible provider")
    payload = json.dumps(
        {
            "model": MODEL_NAME,
            "messages": messages,
            "temperature": 0.7,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        MODEL_API_BASE_URL.rstrip("/") + "/chat/completions",
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {MODEL_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(request, timeout=90) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]


def complete_ollama(messages: list[dict[str, str]]) -> str:
    payload = json.dumps(
        {
            "model": MODEL_NAME,
            "messages": messages,
            "stream": False,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        OLLAMA_API_BASE_URL.rstrip("/") + "/api/chat",
        data=payload,
        method="POST",
        headers=ollama_headers(),
    )
    with urlopen(request, timeout=180) as response:
        data = json.loads(response.read().decode("utf-8"))
    return data["message"]["content"]


def ollama_headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if OLLAMA_API_KEY:
        headers["Authorization"] = f"Bearer {OLLAMA_API_KEY}"
    return headers


def provider_mode() -> str:
    if MODEL_PROVIDER == "ollama":
        return "ollama"
    if MODEL_PROVIDER == "openai" or MODEL_API_KEY:
        return "api"
    return "mock"


def mock_response(messages: list[dict[str, str]]) -> str:
    system = messages[0].get("content", "") if messages else ""
    last_user_text = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            last_user_text = message.get("content", "")
            break
    if "Активная личность: ALIEN" in system:
        return f"**Наблюдение:** Окно приняло ноту.\n**Объяснение:** Тестовый ответ без внешней модели: {last_user_text[:220]}"
    return f"**Наблюдение:** Запрос принят.\n**Рекомендуемое действие:** Тестовый ответ без внешней модели: {last_user_text[:220]}"


def valid_messages(value: object) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        if item.get("role") not in {"system", "user", "assistant"}:
            return False
        if not isinstance(item.get("content"), str) or not item["content"].strip():
            return False
    return True


class AssistantHandler(BaseHTTPRequestHandler):
    server_version = "AIAssistantSimple/0.6"

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_json({"error": "Not found"}, status=404)
            return
        self.send_json(
            {
                "ok": True,
                "server": "simple",
                "model_mode": provider_mode(),
                "message_endpoint": "/v1/message",
                "history_endpoint": "/v1/history",
                "snapshot_endpoint": "/v1/snapshot",
                "sync_endpoint": "/v1/sync",
                "logs_endpoint": "/v1/logs",
                "smart_memory": True,
                "render_blocks": True,
                "timed_memory": True,
                "telegram_webhook": bool(TG_BOT_API_KEY),
                "server_time_utc": current_times()[0],
                "server_timezone": ASSISTANT_TIMEZONE,
            }
        )

    def do_POST(self) -> None:
        if self.path == "/v1/telegram/webhook":
            if not TG_BOT_API_KEY:
                self.send_json({"error": "Telegram is not configured"}, status=404)
                return
            if not self.telegram_authorized():
                self.send_json({"error": "Unauthorized"}, status=401)
                return
            try:
                body = self.read_json_body()
                self.send_json(handle_telegram_update(body))
            except ValueError as error:
                self.send_json({"error": str(error)}, status=400)
            except (HTTPError, URLError, TimeoutError, OSError, RuntimeError, KeyError, json.JSONDecodeError) as error:
                self.send_json({"error": f"Telegram request failed: {error}"}, status=502)
            return

        if self.path not in {"/v1/chat/simple", "/v1/message", "/v1/history", "/v1/snapshot", "/v1/sync", "/v1/logs"}:
            self.send_json({"error": "Not found"}, status=404)
            return
        if not self.authorized():
            self.send_json({"error": "Unauthorized"}, status=401)
            return

        try:
            body = self.read_json_body()
        except ValueError as error:
            self.send_json({"error": str(error)}, status=400)
            return

        try:
            if self.path == "/v1/history":
                self.send_json(handle_history_request(body))
                return
            if self.path == "/v1/snapshot":
                self.send_json(handle_snapshot_request(body))
                return
            if self.path == "/v1/sync":
                self.send_json(handle_sync_request(body))
                return
            if self.path == "/v1/logs":
                self.send_json(handle_logs_request(body))
                return
            if self.path == "/v1/message":
                self.send_json(handle_message_request(body))
                return

            messages = body.get("messages")
            if not valid_messages(messages):
                self.send_json({"error": "Expected non-empty messages[] with role and content"}, status=422)
                return
            text, model_mode = complete(messages)
        except ValueError as error:
            self.send_json({"error": str(error)}, status=422)
            return
        except (HTTPError, URLError, TimeoutError, OSError, RuntimeError, KeyError, json.JSONDecodeError) as error:
            audit_event("request_failed", path=self.path, error_type=type(error).__name__, error_preview=str(error))
            self.send_json({"error": f"Model request failed: {error}", "error_type": type(error).__name__}, status=502)
            return
        except Exception as error:
            audit_event("request_failed", path=self.path, error_type=type(error).__name__, error_preview=str(error))
            self.send_json({"error": "Server request failed", "error_type": type(error).__name__, "detail": text_preview(str(error), 300)}, status=500)
            return

        self.send_json({"text": text, "model_mode": model_mode})

    def read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            raise ValueError("Empty body")
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as error:
            raise ValueError("Invalid JSON") from error
        if not isinstance(data, dict):
            raise ValueError("JSON body must be an object")
        return data

    def authorized(self) -> bool:
        return self.headers.get("Authorization") == f"Bearer {API_TOKEN}"

    def telegram_authorized(self) -> bool:
        if not TG_WEBHOOK_SECRET:
            return True
        return self.headers.get("X-Telegram-Bot-Api-Secret-Token") == TG_WEBHOOK_SECRET

    def send_json(self, payload: dict[str, object], status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}")


def main() -> None:
    storage.init()
    server = ThreadingHTTPServer((HOST, PORT), AssistantHandler)
    print(f"AI Assistant simple server: http://{HOST}:{PORT}", flush=True)
    print("Endpoints: POST /v1/message, POST /v1/history, POST /v1/snapshot, POST /v1/sync, POST /v1/telegram/webhook, POST /v1/chat/simple", flush=True)
    print("Model mode:", provider_mode(), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()












