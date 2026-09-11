from __future__ import annotations

import json
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, scrolledtext
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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


load_local_env()
API_URL = os.getenv("ASSISTANT_API_URL", "https://ai-assistant-4yn0.onrender.com/v1/message")
API_TOKEN = os.getenv("APP_API_TOKEN", "dev-token")
CLIENT_ID = os.getenv("ASSISTANT_CLIENT_ID", "desktop")
CONVERSATION_ID = os.getenv("ASSISTANT_CONVERSATION_ID", "default")


class ChatApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AI Assistant")
        self.geometry("760x620")
        self.minsize(520, 420)

        self.results: queue.Queue[tuple[str, str]] = queue.Queue()
        self.persona = "ANA"

        self.history = scrolledtext.ScrolledText(self, wrap=tk.WORD, state=tk.DISABLED)
        self.history.pack(fill=tk.BOTH, expand=True, padx=12, pady=(12, 8))

        bottom = tk.Frame(self)
        bottom.pack(fill=tk.X, padx=12, pady=(0, 12))

        self.input = tk.Text(bottom, height=4, wrap=tk.WORD)
        self.input.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input.bind("<Return>", self._handle_enter)
        self.input.bind("<Shift-Return>", self._handle_shift_enter)

        self.send_button = tk.Button(bottom, text="Отправить", command=self.send_message)
        self.send_button.pack(side=tk.RIGHT, padx=(8, 0), fill=tk.Y)

        self._append("Система", "Готово. Режим по умолчанию: ANA. Команды: /ana и /alien.")
        self.after(100, self._poll_results)

    def _handle_enter(self, event: object | None = None) -> str:
        return self.send_message(event)

    def _handle_shift_enter(self, event: object | None = None) -> str:
        self.input.insert(tk.INSERT, "\n")
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
        else:
            messagebox.showerror("Ошибка запроса", text)
            self._append("Ошибка", text)

        self.after(100, self._poll_results)

    def _append(self, author: str, text: str) -> None:
        self.history.configure(state=tk.NORMAL)
        self.history.insert(tk.END, f"{author}:\n{text}\n\n")
        self.history.configure(state=tk.DISABLED)
        self.history.see(tk.END)

    def _set_waiting(self, waiting: bool) -> None:
        self.send_button.configure(state=tk.DISABLED if waiting else tk.NORMAL)
        self.send_button.configure(text="Ждём..." if waiting else "Отправить")


if __name__ == "__main__":
    app = ChatApp()
    app.mainloop()
