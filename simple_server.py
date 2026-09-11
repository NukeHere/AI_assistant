from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.personas import (
    Persona,
    build_system_prompt,
    confirmation_for,
    detect_persona_switch,
    extract_alien_glossary_terms,
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
DEFAULT_CLIENT_ID = os.getenv("DEFAULT_CLIENT_ID", "desktop")
DEFAULT_CONVERSATION_ID = os.getenv("DEFAULT_CONVERSATION_ID", "default")

storage = Storage(DATABASE_PATH)


def complete(messages: list[dict[str, str]]) -> tuple[str, str]:
    if MODEL_PROVIDER == "ollama":
        return complete_ollama(messages), "ollama"
    if MODEL_PROVIDER == "openai" or MODEL_API_KEY:
        return complete_openai_compatible(messages), "api"
    if MODEL_PROVIDER != "mock":
        raise RuntimeError(f"Unknown MODEL_PROVIDER: {MODEL_PROVIDER}")
    return mock_response(messages), "mock"


def handle_message_request(body: dict[str, object]) -> dict[str, object]:
    client_id = str(body.get("client_id") or DEFAULT_CLIENT_ID).strip()
    conversation_id = str(body.get("conversation_id") or DEFAULT_CONVERSATION_ID).strip()
    text = str(body.get("text") or "").strip()

    if not client_id:
        raise ValueError("client_id is required")
    if not conversation_id:
        raise ValueError("conversation_id is required")
    if not text:
        raise ValueError("text is required")

    conversation = storage.ensure_conversation(client_id, conversation_id)
    active_persona = Persona(conversation["persona"])

    switched_persona = detect_persona_switch(text)
    if switched_persona is not None:
        storage.set_persona(client_id, conversation_id, switched_persona)
        storage.add_message(conversation_id, "user", text)
        answer = confirmation_for(switched_persona)
        storage.add_message(conversation_id, "assistant", answer)
        return {
            "conversation_id": conversation_id,
            "persona": switched_persona.value,
            "switched": True,
            "text": answer,
            "model_mode": provider_mode(),
        }

    memories = storage.memories_for_prompt(client_id)
    alien_glossary = storage.alien_glossary(conversation_id)
    system_prompt = build_system_prompt(active_persona, alien_glossary)
    if memories:
        system_prompt += "\n\nRelevant long-term memory:\n" + "\n".join(f"- {item}" for item in memories)

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(storage.recent_messages(conversation_id, CLIENT_HISTORY_LIMIT))
    messages.append({"role": "user", "content": text})

    storage.add_message(conversation_id, "user", text)
    if active_persona == Persona.ALIEN:
        storage.update_alien_glossary(client_id, conversation_id, extract_alien_glossary_terms(text))
    answer, model_mode = complete(messages)
    storage.add_message(conversation_id, "assistant", answer)

    return {
        "conversation_id": conversation_id,
        "persona": active_persona.value,
        "switched": False,
        "text": answer,
        "model_mode": model_mode,
    }


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
    if "Active persona: ALIEN" in system:
        return f"Окно приняло ноту. Тестовый ответ без внешней модели: {last_user_text[:220]}"
    return f"Запрос принят. Тестовый ответ без внешней модели: {last_user_text[:220]}"


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
    server_version = "AIAssistantSimple/0.2"

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
            }
        )

    def do_POST(self) -> None:
        if self.path not in {"/v1/chat/simple", "/v1/message"}:
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
            self.send_json({"error": f"Model request failed: {error}"}, status=502)
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
    print("Endpoints: POST /v1/message, POST /v1/chat/simple", flush=True)
    print("Model mode:", provider_mode(), flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
