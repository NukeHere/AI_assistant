"""Deterministic group mentions, independent of AI and quiet mode."""
import html
import logging
import re

from app.telegram_moderation import api_call


def is_all_mention(text):
    return bool(re.search(r"(?<![\w@])@all(?!\w)", text, re.IGNORECASE))


def track_group_members(storage, update):
    event = update.get("chat_member")
    if isinstance(event, dict):
        chat = event.get("chat") or {}
        member = event.get("new_chat_member") or {}
        if chat.get("type") in {"group", "supergroup"}:
            active = member.get("status") in {"member", "administrator", "creator"} or (
                member.get("status") == "restricted" and member.get("is_member", False))
            storage.record_telegram_group_member(chat["id"], member.get("user") or {}, active)
        return
    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat") or {}
    if chat.get("type") not in {"group", "supergroup"}:
        return
    chat_id = chat["id"]
    storage.record_telegram_group_member(chat_id, message.get("from") or {})
    for user in message.get("new_chat_members") or []:
        storage.record_telegram_group_member(chat_id, user)
    if message.get("left_chat_member"):
        storage.record_telegram_group_member(chat_id, message["left_chat_member"], False)


def send_all_mentions(storage, chat_id, token, thread_id=None):
    try:
        for member in api_call(token, "getChatAdministrators", {"chat_id": chat_id}):
            storage.record_telegram_group_member(chat_id, member.get("user") or {})
    except Exception:
        logging.getLogger(__name__).warning("Could not refresh administrators for @all")
    mentions = []
    for user in storage.telegram_group_members(chat_id):
        user_id = user["user_id"]
        if not user_id.isdecimal():
            continue
        name = user["username"] or user["display_name"] or user_id
        # ID links keep mentions working for users without usernames.
        mentions.append(f'<a href="tg://user?id={user_id}">@{html.escape(name)}</a>')
    # Stay below both message size and Telegram's per-message entity limits.
    batches, batch = [], []
    for mention in mentions:
        if len(batch) >= 5 or len(" ".join([*batch, mention])) > 3500:
            batches.append(" ".join(batch))
            batch = []
        batch.append(mention)
    if batch:
        batches.append(" ".join(batch))
    for text in batches:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        api_call(token, "sendMessage", payload)
