from __future__ import annotations

import json
import os
import queue
import re
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


STRUCTURED_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?"
    r"(Observation|Diagnostic|Diagnosis|Recommended action|Command action|Action|Explanation|Conclusion)"
    r"(?:\*\*)?\s*:\s*(.*)$",
    re.IGNORECASE,
)

ANA_BLOCK_LABELS = {
    "observation": "НАБЛЮДЕНИЕ",
    "diagnostic": "ДИАГНОСТИКА",
    "diagnosis": "ДИАГНОСТИКА",
    "recommended action": "ДЕЙСТВИЕ",
    "command action": "КОМАНДА",
    "action": "ДЕЙСТВИЕ",
    "explanation": "ОБЪЯСНЕНИЕ",
    "conclusion": "ВЫВОД",
}

ALIEN_BLOCK_LABELS = {
    "observation": "СИГНАЛ",
    "diagnostic": "ДИССОНАНС",
    "diagnosis": "ДИССОНАНС",
    "recommended action": "НОТА ДЕЙСТВИЯ",
    "command action": "КОМАНДНАЯ НОТА",
    "action": "НОТА ДЕЙСТВИЯ",
    "explanation": "ГЛУБИНА",
    "conclusion": "РЕЗОНАНС",
}


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


load_local_env()
API_URL = os.getenv("ASSISTANT_API_URL", "https://ai-assistant-4yn0.onrender.com/v1/message")
API_TOKEN = os.getenv("APP_API_TOKEN", "dev-token")
CLIENT_ID = os.getenv("ASSISTANT_CLIENT_ID", "primary-user")
CONVERSATION_ID = os.getenv("ASSISTANT_CONVERSATION_ID", "default")
LOCAL_DATA_DIR = Path(os.getenv("ASSISTANT_LOCAL_DATA_DIR", Path.home() / ".ai_assistant"))
HISTORY_PATH = LOCAL_DATA_DIR / f"history-{safe_name(CLIENT_ID)}-{safe_name(CONVERSATION_ID)}.json"
SNAPSHOT_PATH = LOCAL_DATA_DIR / f"snapshot-{safe_name(CLIENT_ID)}.json"
HISTORY_KEEP = int(os.getenv("ASSISTANT_LOCAL_HISTORY_KEEP", "400"))


class ChatApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AI Assistant")
        self.geometry("760x620")
        self.minsize(520, 420)

        self.results: queue.Queue[tuple[str, str]] = queue.Queue()
        self.persona = "ANA"
        self.chat_log = self._load_local_history()

        self.history = scrolledtext.ScrolledText(self, wrap=tk.WORD, state=tk.DISABLED)
        self._configure_history_tags()
        self.history.bind("<Button-1>", self._focus_history)
        self.history.bind("<Control-c>", self._copy_history_selection)
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
            for entry in self.chat_log:
                self._append(entry.get("author", "Система"), entry.get("text", ""), persist=False)
            self._append("Система", "Локальная история восстановлена. Контекст сервера продолжает тот же conversation_id.", persist=False)
        else:
            self._append("Система", "Готово. Режим по умолчанию: ANA. Команды: /ana, /alien, запомни: ..., /memory.")
        self.after(100, self._poll_results)
        self.after(300, self._restore_snapshot_async)

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
            if isinstance(item, dict) and isinstance(item.get("author"), str) and isinstance(item.get("text"), str):
                entries.append({"author": item["author"], "text": item["text"]})
        return entries

    def _save_local_history(self) -> None:
        LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_PATH.write_text(json.dumps(self.chat_log[-HISTORY_KEEP:], ensure_ascii=False, indent=2), encoding="utf-8")

    def _snapshot_url(self) -> str | None:
        stripped = API_URL.rstrip("/")
        if stripped.endswith("/v1/message"):
            return stripped[: -len("/v1/message")] + "/v1/snapshot"
        if stripped.endswith("/v1/history"):
            return stripped[: -len("/v1/history")] + "/v1/snapshot"
        return None

    def _restore_snapshot_async(self) -> None:
        if not SNAPSHOT_PATH.exists() or self._snapshot_url() is None:
            return
        thread = threading.Thread(target=self._restore_snapshot, daemon=True)
        thread.start()

    def _restore_snapshot(self) -> None:
        try:
            snapshot = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
            self._snapshot_request({"action": "import", "client_id": CLIENT_ID, "snapshot": snapshot}, timeout=45)
            self.results.put(("system", "Локальный snapshot памяти синхронизирован с сервером."))
        except (OSError, json.JSONDecodeError, HTTPError, URLError, TimeoutError, KeyError, ValueError):
            return

    def _backup_snapshot_async(self) -> None:
        if self._snapshot_url() is None:
            return
        thread = threading.Thread(target=self._backup_snapshot, daemon=True)
        thread.start()

    def _backup_snapshot(self) -> None:
        try:
            data = self._snapshot_request({"action": "export", "client_id": CLIENT_ID}, timeout=45)
            snapshot = data.get("snapshot")
            if isinstance(snapshot, dict):
                LOCAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
                SNAPSHOT_PATH.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, HTTPError, URLError, TimeoutError, KeyError, ValueError):
            return

    def _snapshot_request(self, payload_body: dict[str, object], timeout: int) -> dict[str, object]:
        snapshot_url = self._snapshot_url()
        if snapshot_url is None:
            raise ValueError("Snapshot endpoint is unavailable for this API_URL")
        payload = json.dumps(payload_body, ensure_ascii=False).encode("utf-8")
        request = Request(
            snapshot_url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {API_TOKEN}",
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Snapshot response must be an object")
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

    def send_message(self, event: object | None = None) -> str:
        text = self.input.get("1.0", tk.END).strip()
        if not text:
            return "break"

        self.input.delete("1.0", tk.END)
        self._append("Вы", text)
        self._set_waiting(True)

        thread = threading.Thread(target=self._request_answer, args=(text,), daemon=True)
        thread.start()
        return "break"

    def _request_answer(self, text: str) -> None:
        try:
            if API_URL.rstrip("/").endswith("/v1/chat/simple"):
                payload_body = {
                    "messages": [
                        {
                            "role": "system",
                            "content": "You are a helpful AI assistant. Answer clearly and concisely.",
                        },
                        {"role": "user", "content": text},
                    ]
                }
            else:
                payload_body = {
                    "client_id": CLIENT_ID,
                    "conversation_id": CONVERSATION_ID,
                    "text": text,
                    "input_type": "text",
                }
            payload = json.dumps(payload_body, ensure_ascii=False).encode("utf-8")
            request = Request(
                API_URL,
                data=payload,
                method="POST",
                headers={
                    "Authorization": f"Bearer {API_TOKEN}",
                    "Content-Type": "application/json",
                },
            )
            with urlopen(request, timeout=90) as response:
                data = json.loads(response.read().decode("utf-8"))
            persona = data.get("persona")
            if isinstance(persona, str):
                self.persona = persona
            self._backup_snapshot_async()
            self.results.put(("assistant", data["text"]))
        except HTTPError as error:
            details = error.read().decode("utf-8", errors="replace")
            self.results.put(("error", f"HTTP {error.code}: {details}"))
        except (URLError, TimeoutError, OSError) as error:
            self.results.put(("error", str(error)))

    def _poll_results(self) -> None:
        try:
            kind, text = self.results.get_nowait()
        except queue.Empty:
            self.after(100, self._poll_results)
            return

        self._set_waiting(False)
        if kind == "assistant":
            self._append(self.persona, text)
        elif kind == "system":
            self._append("Система", text, persist=False)
        else:
            messagebox.showerror("Ошибка запроса", text)
            self._append("Ошибка", text)

        self.after(100, self._poll_results)
    def _append(self, author: str, text: str, persist: bool = True) -> None:
        if persist:
            self.chat_log.append({"author": author, "text": text})
            self._save_local_history()

        self.history.configure(state=tk.NORMAL)
        author_tag = self._author_tag(author)
        self.history.insert(tk.END, f"{author}:\n", ("author", author_tag))
        if author in {"ANA", "ALIEN"}:
            self._insert_assistant_text(author, text)
        else:
            self.history.insert(tk.END, f"{text}\n\n", (author_tag,))
        self.history.configure(state=tk.DISABLED)
        self.history.see(tk.END)

    def _insert_assistant_text(self, author: str, text: str) -> None:
        block_tag = "block_alien" if author == "ALIEN" else "block_ana"
        label_tag = "block_label_alien" if author == "ALIEN" else "block_label_ana"
        labels = ALIEN_BLOCK_LABELS if author == "ALIEN" else ANA_BLOCK_LABELS

        for raw_line in text.splitlines() or [""]:
            match = STRUCTURED_LINE_RE.match(raw_line)
            if match:
                label_key = match.group(1).lower()
                rest = match.group(2).strip()
                label = labels.get(label_key, label_key.upper())
                self.history.insert(tk.END, f"  {label}\n", (block_tag, label_tag))
                if rest:
                    self.history.insert(tk.END, f"  {rest}\n", (block_tag,))
            else:
                self.history.insert(tk.END, f"{raw_line}\n", (self._author_tag(author),))
        self.history.insert(tk.END, "\n")

    def _author_tag(self, author: str) -> str:
        if author == "Система":
            return "system"
        if author == "Вы":
            return "user"
        if author == "ALIEN":
            return "alien"
        return "ana"

    def _set_waiting(self, waiting: bool) -> None:
        self.send_button.configure(state=tk.DISABLED if waiting else tk.NORMAL)
        self.send_button.configure(text="Ждём..." if waiting else "Отправить")


if __name__ == "__main__":
    app = ChatApp()
    app.mainloop()
