from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import threading
import tkinter as tk
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tkinter import messagebox, scrolledtext
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.text_render import strip_inline_markdown, structured_parts


def load_local_env() -> None:
    env_path = Path(__file__).resolve().with_name(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "_", value).strip("._") or "default"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


load_local_env()
API_URL = os.getenv("ASSISTANT_API_URL", "https://ai-assistant-4yn0.onrender.com/v1/message")
API_TOKEN = os.getenv("APP_API_TOKEN", "dev-token")
CLIENT_ID = os.getenv("ASSISTANT_CLIENT_ID", "primary-user")
DEVICE_ID = os.getenv("ASSISTANT_DEVICE_ID", f"desktop-{safe_name(os.environ.get('COMPUTERNAME', 'windows'))}")
CONVERSATION_ID = os.getenv("ASSISTANT_CONVERSATION_ID", "default")
LOCAL_DATA_DIR = Path(os.getenv("ASSISTANT_LOCAL_DATA_DIR", Path.home() / ".ai_assistant"))
HISTORY_PATH = LOCAL_DATA_DIR / f"history-{safe_name(CLIENT_ID)}-{safe_name(CONVERSATION_ID)}.json"
SNAPSHOT_PATH = LOCAL_DATA_DIR / f"snapshot-{safe_name(CLIENT_ID)}.json"
NOTIFIED_TIMERS_PATH = LOCAL_DATA_DIR / f"notified-timers-{safe_name(CLIENT_ID)}-{safe_name(CONVERSATION_ID)}.json"
HISTORY_KEEP = int(os.getenv("ASSISTANT_LOCAL_HISTORY_KEEP", "400"))
SYNC_INTERVAL_MS = max(5, int(os.getenv("ASSISTANT_SYNC_INTERVAL_SECONDS", "10"))) * 1000


class ChatApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AI Assistant")
        self.geometry("780x640")
        self.minsize(540, 440)

        self.results: queue.Queue[tuple[str, object]] = queue.Queue()
        self.persona = "ANA"
        self.chat_log = self._load_local_history()
        self.notified_timer_ids = self._load_notified_timer_ids()
        self._sync_running = False

        quick = tk.Frame(self)
        quick.pack(fill=tk.X, padx=12, pady=(8, 0))
        tk.Button(quick, text="Sync", command=self._sync_history_async).pack(side=tk.LEFT)
        tk.Button(quick, text="↓ вниз", command=self._scroll_to_bottom).pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(quick, text="ANA", command=lambda: self._send_quick("/ana")).pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(quick, text="ALIEN", command=lambda: self._send_quick("/alien")).pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(quick, text="Память", command=lambda: self._send_quick("/memory")).pack(side=tk.LEFT, padx=(6, 0))
        tk.Button(quick, text="Функции", command=lambda: self._send_quick("/functions")).pack(side=tk.LEFT, padx=(6, 0))

        self.history = scrolledtext.ScrolledText(self, wrap=tk.WORD, state=tk.DISABLED)
        self._configure_history_tags()
        self.history.bind("<Button-1>", self._focus_history)
        self.history.bind("<Control-c>", self._copy_history_selection)
        self.history.bind("<Button-3>", self._show_history_menu)
        self.history.bind("<Button-2>", self._show_history_menu)
        self.bind_all("<Control-c>", self._copy_history_or_default, add="+")
        self.bind_all("<Control-C>", self._copy_history_or_default, add="+")
        self.history_menu = tk.Menu(self, tearoff=False)
        self.history_menu.add_command(label="Копировать", command=self._copy_history_selection)
        self.history_menu.add_command(label="Копировать всё", command=self._copy_all_history)
        self.history.bind("<Control-C>", self._copy_history_selection)
        self.history.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 8))

        bottom = tk.Frame(self)
        bottom.pack(fill=tk.X, padx=12, pady=(0, 12))

        self.input = tk.Text(bottom, height=4, wrap=tk.WORD)
        self.input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input.bind("<Return>", self._handle_enter)
        self.input.bind("<Shift-Return>", self._handle_shift_enter)

        self.send_button = tk.Button(bottom, text="Отправить", command=self.send_message)
        self.send_button.pack(side=tk.RIGHT, padx=(8, 0), fill=tk.Y)

        if self.chat_log:
            self._render_history()
            self._append_system("Локальная история восстановлена. Сверяю с сервером.")
        else:
            self._append_system("Готово. Режим по умолчанию: ANA. Команды: /ana, /alien, запомни: ..., /memory, /timers, /functions.")
        self.after(100, self._poll_results)
        self.after(200, self._sync_history_async)
        self.after(500, self._restore_snapshot_async)
        self.after(SYNC_INTERVAL_MS, self._periodic_sync)

    def _configure_history_tags(self) -> None:
        self.history.tag_configure("author", font=("TkDefaultFont", 10, "bold"))
        self.history.tag_configure("system", foreground="#666666")
        self.history.tag_configure("user", foreground="#1f2937")
        self.history.tag_configure("ana", foreground="#111827")
        self.history.tag_configure("alien", foreground="#3b1d70")
        self.history.tag_configure("block_ana", background="#eef3f8", lmargin1=18, lmargin2=18, spacing1=3, spacing3=3)
        self.history.tag_configure("block_alien", background="#f3eef8", lmargin1=18, lmargin2=18, spacing1=3, spacing3=3)
        self.history.tag_configure("block_label_ana", foreground="#25506f", font=("TkDefaultFont", 8, "bold"))
        self.history.tag_configure("block_label_alien", foreground="#5b2a86", font=("TkDefaultFont", 8, "bold"))

    def _load_local_history(self) -> list[dict[str, str]]:
        if not HISTORY_PATH.exists():
            return []
        try:
            data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(data, list):
            return []
        entries: list[dict[str, str]] = []
        for item in data[-HISTORY_KEEP:]:
            if not isinstance(item, dict):
                continue
            entry = self._normalize_entry(item)
            if entry:
                entries.append(entry)
        return entries

    def _normalize_entry(self, item: dict[str, object]) -> dict[str, str] | None:
        author = str(item.get("author") or "")
        text = str(item.get("text") or item.get("content") or "")
        role = str(item.get("role") or self._role_for_author(author))
        if not text or role not in {"system", "user", "assistant"}:
            return None
        if not author:
            author = self._author_for_role(role, str(item.get("persona") or self.persona))
        created_at = str(item.get("created_at") or item.get("client_created_at") or utc_now())
        message_id = str(item.get("message_id") or item.get("message_uid") or "")
        if not message_id:
            seed = "|".join([CONVERSATION_ID, role, text, created_at])
            message_id = "local-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]
        return {
            "message_id": message_id,
            "conversation_id": str(item.get("conversation_id") or CONVERSATION_ID),
            "role": role,
            "author": author,
            "text": text,
            "created_at": created_at,
            "client_created_at": str(item.get("client_created_at") or created_at),
            "device_id": str(item.get("device_id") or DEVICE_ID),
        }

    def _save_local_history(self) -> None:
        LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_PATH.write_text(json.dumps(self.chat_log[-HISTORY_KEEP:], ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_notified_timer_ids(self) -> set[str]:
        try:
            raw = json.loads(NOTIFIED_TIMERS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return set()
        if not isinstance(raw, list):
            return set()
        return {str(item) for item in raw if isinstance(item, str) and item}

    def _save_notified_timer_ids(self) -> None:
        LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
        recent_ids = sorted(self.notified_timer_ids)[-500:]
        NOTIFIED_TIMERS_PATH.write_text(json.dumps(recent_ids, ensure_ascii=False, indent=2), encoding="utf-8")

    def _is_timed_memory_entry(self, entry: dict[str, str]) -> bool:
        if entry.get("role") != "system":
            return False
        text = entry.get("text", "")
        return "⏰" in text or "Временная память сработала" in text

    def _maybe_notify_timer(self, entry: dict[str, str]) -> None:
        if not self._is_timed_memory_entry(entry):
            return
        message_id = entry.get("message_id", "")
        if not message_id or message_id in self.notified_timer_ids:
            return
        self.notified_timer_ids.add(message_id)
        self._save_notified_timer_ids()
        self.after_idle(lambda text=entry.get("text", ""): self._show_timer_notification(text))

    def _show_timer_notification(self, text: str) -> None:
        title = "AI Assistant — напоминание"
        body = strip_inline_markdown(text).strip() or "Сработала временная память."
        try:
            self.bell()
        except tk.TclError:
            pass
        try:
            popup = tk.Toplevel(self)
            popup.title(title)
            popup.attributes("-topmost", True)
            popup.resizable(False, False)
            popup.geometry("420x170+80+80")
            tk.Label(popup, text=title, font=("TkDefaultFont", 11, "bold"), anchor="w").pack(fill=tk.X, padx=14, pady=(12, 4))
            tk.Label(popup, text=body, justify=tk.LEFT, wraplength=380, anchor="w").pack(fill=tk.BOTH, expand=True, padx=14, pady=4)
            tk.Button(popup, text="ОК", command=popup.destroy).pack(pady=(0, 12))
            popup.after(15000, lambda: popup.winfo_exists() and popup.destroy())
        except tk.TclError:
            messagebox.showinfo(title, body)

    def _endpoint_url(self, endpoint: str) -> str | None:
        stripped = API_URL.rstrip("/")
        for suffix in ["/v1/message", "/v1/history", "/v1/snapshot", "/v1/sync", "/v1/chat/simple"]:
            if stripped.endswith(suffix):
                return stripped[: -len(suffix)] + endpoint
        return stripped + endpoint if stripped.startswith("http") else None

    def _snapshot_url(self) -> str | None:
        return self._endpoint_url("/v1/snapshot")

    def _sync_url(self) -> str | None:
        return self._endpoint_url("/v1/sync")

    def _periodic_sync(self) -> None:
        self._sync_history_async()
        self.after(SYNC_INTERVAL_MS, self._periodic_sync)
    def _sync_history_async(self) -> None:
        if self._sync_url() is None or self._sync_running:
            return
        self._sync_running = True
        threading.Thread(target=self._sync_history_with_server, daemon=True).start()

    def _sync_history_with_server(self) -> None:
        try:
            payload = {
                "client_id": CLIENT_ID,
                "conversation_id": CONVERSATION_ID,
                "device_id": DEVICE_ID,
                "limit": HISTORY_KEEP,
                "messages": [self._entry_to_event(entry) for entry in self.chat_log if entry.get("role") != "system"],
            }
            data = self._post_json(self._sync_url(), payload, timeout=45)
            self.results.put(("sync", data))
        except (OSError, HTTPError, URLError, TimeoutError, KeyError, ValueError, json.JSONDecodeError):
            self.results.put(("sync_done", {}))

    def _entry_to_event(self, entry: dict[str, str]) -> dict[str, str]:
        return {
            "message_id": entry["message_id"],
            "conversation_id": entry.get("conversation_id", CONVERSATION_ID),
            "role": entry["role"],
            "content": entry["text"],
            "created_at": entry.get("created_at", utc_now()),
            "client_created_at": entry.get("client_created_at", entry.get("created_at", utc_now())),
            "device_id": entry.get("device_id", DEVICE_ID),
        }

    def _restore_snapshot_async(self) -> None:
        if not SNAPSHOT_PATH.exists() or self._snapshot_url() is None:
            return
        threading.Thread(target=self._restore_snapshot, daemon=True).start()

    def _restore_snapshot(self) -> None:
        try:
            snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
            self._post_json(self._snapshot_url(), {"action": "import", "client_id": CLIENT_ID, "snapshot": snapshot}, timeout=45)
            self._sync_history_with_server()
        except (OSError, json.JSONDecodeError, HTTPError, URLError, TimeoutError, KeyError, ValueError):
            return

    def _backup_snapshot_async(self) -> None:
        if self._snapshot_url() is None:
            return
        threading.Thread(target=self._backup_snapshot, daemon=True).start()

    def _backup_snapshot(self) -> None:
        try:
            data = self._post_json(self._snapshot_url(), {"action": "export", "client_id": CLIENT_ID}, timeout=45)
            snapshot = data.get("snapshot")
            if isinstance(snapshot, dict):
                LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
                SNAPSHOT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, HTTPError, URLError, TimeoutError, KeyError, ValueError):
            return

    def _post_json(self, url: str | None, payload_body: dict[str, object], timeout: int) -> dict[str, object]:
        if url is None:
            raise ValueError("Endpoint is unavailable for this API_URL")
        payload = json.dumps(payload_body, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=payload,
            method="POST",
            headers={"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"},
        )
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Response must be an object")
        return data

    def _handle_enter(self, event: object | None = None) -> str:
        return self.send_message(event)

    def _handle_shift_enter(self, event: object | None = None) -> str:
        self.input.insert(tk.INSERT, "\n")
        return "break"

    def _focus_history(self, event: object | None = None) -> None:
        self.history.focus_set()

    def _copy_history_selection(self, event: object | None = None) -> str:
        try:
            selected_text = self.history.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            return "break"
        self.clipboard_clear()
        self.clipboard_append(selected_text)
        return "break"

    def _copy_history_or_default(self, event: object | None = None) -> str | None:
        try:
            self.history.get(tk.SEL_FIRST, tk.SEL_LAST)
        except tk.TclError:
            return None
        return self._copy_history_selection(event)

    def _show_history_menu(self, event: tk.Event) -> str:
        try:
            self.history_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.history_menu.grab_release()
        return "break"

    def _copy_all_history(self) -> str:
        text = self.history.get("1.0", tk.END).strip()
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
        return "break"

    def _send_quick(self, text: str) -> None:
        self.input.delete("1.0", tk.END)
        self.input.insert("1.0", text)
        self.send_message()

    def send_message(self, event: object | None = None) -> str:
        text = self.input.get("1.0", tk.END).strip()
        if not text:
            return "break"
        self.input.delete("1.0", tk.END)
        self._append_entry(self._new_entry("user", "Вы", text))
        self._set_waiting(True)
        threading.Thread(target=self._request_answer, args=(text,), daemon=True).start()
        return "break"

    def _request_answer(self, text: str) -> None:
        try:
            if API_URL.rstrip("/").endswith("/v1/chat/simple"):
                payload_body = {"messages": [{"role": "system", "content": "You are a helpful AI assistant."}, {"role": "user", "content": text}]}
            else:
                payload_body = {"client_id": CLIENT_ID, "conversation_id": CONVERSATION_ID, "text": text, "input_type": "text"}
            data = self._post_json(API_URL, payload_body, timeout=120)
            persona = data.get("persona")
            if isinstance(persona, str):
                self.persona = persona
            answer = str(data.get("text") or "")
            self.results.put(("assistant", {"text": answer, "persona": self.persona, "blocks": data.get("render_blocks")}))
            self._backup_snapshot_async()
        except HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            self.results.put(("error", f"HTTP {error.code}: {details}"))
        except (URLError, TimeoutError, OSError, ValueError) as error:
            self.results.put(("error", str(error)))

    def _poll_results(self) -> None:
        try:
            kind, payload = self.results.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_results)
            return

        if kind in {"assistant", "error"}:
            self._set_waiting(False)
        if kind == "assistant" and isinstance(payload, dict):
            self._append_entry(self._new_entry("assistant", str(payload.get("persona") or self.persona), str(payload.get("text") or "")))
            self._sync_history_async()
        elif kind == "sync" and isinstance(payload, dict):
            self._sync_running = False
            self._merge_server_messages(payload)
        elif kind == "sync_done":
            self._sync_running = False
        elif kind == "system" and isinstance(payload, str):
            self._append_system(payload)
        else:
            self._set_waiting(False)
            messagebox.showerror("Ошибка запроса", str(payload))
            self._append_entry(self._new_entry("system", "Ошибка", str(payload)))

        self.after(100, self._poll_results)

    def _merge_server_messages(self, payload: dict[str, object]) -> None:
        persona = payload.get("persona")
        if isinstance(persona, str):
            self.persona = persona
        raw_messages = payload.get("messages")
        if not isinstance(raw_messages, list):
            return
        entries: list[dict[str, str]] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "")
            content = str(item.get("content") or "")
            if role not in {"user", "assistant", "system"} or not content:
                continue
            entries.append(self._normalize_entry({
                "message_id": item.get("message_id") or item.get("message_uid"),
                "conversation_id": item.get("conversation_id") or CONVERSATION_ID,
                "role": role,
                "author": self._author_for_role(role, str(payload.get("persona") or self.persona)),
                "text": content,
                "created_at": item.get("created_at") or utc_now(),
                "client_created_at": item.get("client_created_at") or item.get("created_at") or utc_now(),
                "device_id": item.get("device_id") or "server",
            }) or {})
        known_ids = {entry.get("message_id", "") for entry in self.chat_log}
        for entry in entries:
            if entry and entry.get("message_id", "") not in known_ids:
                self._maybe_notify_timer(entry)
        before = json.dumps(self.chat_log, ensure_ascii=False, sort_keys=True)
        self.chat_log = self._merge_entries(self.chat_log, entries)[-HISTORY_KEEP:]
        after = json.dumps(self.chat_log, ensure_ascii=False, sort_keys=True)
        if after != before:
            self._save_local_history()
            self._render_history()

    def _merge_entries(self, local: list[dict[str, str]], remote: list[dict[str, str]]) -> list[dict[str, str]]:
        merged: list[dict[str, str]] = []
        seen_ids: set[str] = set()
        seen_hashes: set[tuple[str, str, str]] = set()
        for source in (local, remote):
            for entry in source:
                normalized = self._normalize_entry(entry)
                if not normalized:
                    continue
                key_id = normalized["message_id"]
                key_hash = (normalized["conversation_id"], normalized["role"], hashlib.sha256(normalized["text"].encode("utf-8")).hexdigest())
                if key_id in seen_ids or key_hash in seen_hashes:
                    continue
                seen_ids.add(key_id)
                seen_hashes.add(key_hash)
                merged.append(normalized)
        merged.sort(key=lambda item: (item.get("created_at", ""), item.get("message_id", "")))
        return merged

    def _render_history(self) -> None:
        self.history.configure(state=tk.NORMAL)
        self.history.delete("1.0", tk.END)
        self.history.configure(state=tk.DISABLED)
        for entry in self.chat_log:
            self._draw_entry(entry)
        self._scroll_to_bottom()

    def _append_system(self, text: str) -> None:
        self.history.configure(state=tk.NORMAL)
        self.history.insert(tk.END, f"Система:\n{text}\n\n", ("system",))
        self.history.configure(state=tk.DISABLED)
        self._scroll_to_bottom()

    def _append_entry(self, entry: dict[str, str]) -> None:
        self.chat_log.append(entry)
        self.chat_log = self._merge_entries([], self.chat_log)[-HISTORY_KEEP:]
        self._save_local_history()
        self._draw_entry(entry)
        self._scroll_to_bottom()

    def _draw_entry(self, entry: dict[str, str]) -> None:
        author = entry.get("author", "Система")
        text = entry.get("text", "")
        self.history.configure(state=tk.NORMAL)
        author_tag = self._author_tag(author)
        self.history.insert(tk.END, f"{author}:\n", ("author", author_tag))
        if author in {"ANA", "ALIEN"}:
            self._insert_assistant_text(author, text)
        else:
            self.history.insert(tk.END, f"{text}\n\n", (author_tag,))
        self.history.configure(state=tk.DISABLED)

    def _insert_assistant_text(self, author: str, text: str) -> None:
        block_tag = "block_alien" if author == "ALIEN" else "block_ana"
        label_tag = "block_label_alien" if author == "ALIEN" else "block_label_ana"
        for raw_line in text.splitlines() or [""]:
            parts = structured_parts(author, raw_line)
            if parts:
                label, rest = parts
                self.history.insert(tk.END, f"  {label}\n", (block_tag, label_tag))
                if rest:
                    self.history.insert(tk.END, f"  {strip_inline_markdown(rest)}\n", (block_tag,))
            else:
                cleaned = strip_inline_markdown(raw_line)
                if cleaned:
                    self.history.insert(tk.END, f"{cleaned}\n", (self._author_tag(author),))
        self.history.insert(tk.END, "\n")

    def _new_entry(self, role: str, author: str, text: str) -> dict[str, str]:
        created_at = utc_now()
        return {
            "message_id": f"{DEVICE_ID}-{uuid.uuid4().hex}",
            "conversation_id": CONVERSATION_ID,
            "role": role,
            "author": author,
            "text": text,
            "created_at": created_at,
            "client_created_at": created_at,
            "device_id": DEVICE_ID,
        }

    def _role_for_author(self, author: str) -> str:
        if author == "Вы":
            return "user"
        if author in {"ANA", "ALIEN"}:
            return "assistant"
        return "system"

    def _author_for_role(self, role: str, persona: str) -> str:
        if role == "user":
            return "Вы"
        if role == "assistant":
            return persona if persona in {"ANA", "ALIEN"} else "ANA"
        return "Система"

    def _author_tag(self, author: str) -> str:
        if author == "Система" or author == "Ошибка":
            return "system"
        if author == "Вы":
            return "user"
        if author == "ALIEN":
            return "alien"
        return "ana"

    def _scroll_to_bottom(self) -> None:
        self.after_idle(lambda: self.history.see(tk.END))

    def _set_waiting(self, waiting: bool) -> None:
        self.send_button.configure(state=tk.DISABLED if waiting else tk.NORMAL)
        self.send_button.configure(text="Ждём..." if waiting else "Отправить")


if __name__ == "__main__":
    app = ChatApp()
    app.mainloop()
