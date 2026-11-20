"""Owner-controlled persistent Groq model routing."""
import json
import os
import re
import time
import math
from collections import deque
from threading import Lock
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

_usage_lock = Lock()
_usage = {}
INPUT_BUDGET = 5000
OUTPUT_BUDGET = 512
MINUTE_BUDGET = 7200


def estimate_tokens(messages):
    # Conservative UTF-8 estimate for mixed Russian/English chat, including roles.
    return sum(math.ceil(len(str(m.get("content", "")).encode("utf-8")) / 3) + 8 for m in messages)


def bounded_messages(messages):
    result = [{"role": m["role"], "content": m["content"]} for m in messages]
    # Preserve all instructions and the current request. History stays intact in DB.
    while estimate_tokens(result) > INPUT_BUDGET:
        removable = next((i for i, m in enumerate(result[:-1]) if m["role"] != "system"), None)
        if removable is None:
            raise GroqRequestError(413, "Current request and instructions exceed input budget")
        result.pop(removable)
    return result


def reserve_tokens(model, amount):
    if amount > MINUTE_BUDGET:
        raise GroqRequestError(413, "Request exceeds minute budget")
    while True:
        with _usage_lock:
            now = time.monotonic()
            usage = _usage.setdefault(model, deque())
            while usage and now - usage[0][0] >= 61:
                usage.popleft()
            if sum(tokens for _, tokens in usage) + amount <= MINUTE_BUDGET:
                usage.append((now, amount))
                return
            delay = max(0.1, 61 - (now - usage[0][0]))
        time.sleep(delay)


class GroqRequestError(RuntimeError):
    def __init__(self, code, reason):
        super().__init__(f"Groq HTTP {code}: {reason}")
        self.code = code


def models():
    return json.loads(Path(__file__).with_name("models.json").read_text(encoding="utf-8"))


def control_model(storage, user_id, text):
    if str(user_id) != os.getenv("TG_MODEL_OWNER_ID", "1834373176"):
        return None
    text = text.lower().strip()
    if re.search(r"(какие|список|доступные).*модел", text):
        return "Доступные модели\n" + "\n".join(f"{key}: {value['name']}" for key, value in models().items())
    if re.search(r"(какая|текущая|сейчас).*модел", text):
        return "Режим модели: " + storage.model_selection()
    if not re.search(r"(смени|сменить|переключ|используй|выбирай|включи|выбери)", text):
        return None
    if not re.search(r"модел|20\s*b|120\s*b", text):
        return None
    if "автомат" in text:
        selection = "auto"
    elif re.search(r"120\s*b|крут|мощн|сильн", text):
        selection = "120b"
    elif re.search(r"20\s*b|легк|лёгк", text):
        selection = "20b"
    else:
        return "Уточни модель: 20B, 120B или автоматический выбор."
    storage.set_model_selection(selection)
    return "Выбран режим: " + selection


def choose_model(selection, messages):
    if selection != "auto":
        return selection if selection in models() else "20b"
    text = next((m.get("content", "") for m in reversed(messages) if m.get("role") == "user"), "")
    complex_task = len(text) > 1800 or re.search(r"проанализируй|докажи|подробн|исследуй|алгоритм|debug|refactor", text, re.I)
    return "120b" if complex_task else "20b"


def complete_groq(storage, messages):
    token = os.getenv("GROQ_API_KEY", "").strip()
    if not token:
        raise RuntimeError("GROQ_API_KEY is required")
    selection = storage.model_selection()
    chosen = choose_model(selection, messages)
    def request_model(key):
        api_messages = bounded_messages(messages)
        payload = {"model": models()[key]["id"], "messages": api_messages,
                   "temperature": 0.7, "max_completion_tokens": OUTPUT_BUDGET}
        request = Request("https://api.groq.com/openai/v1/chat/completions",
                          data=json.dumps(payload).encode(),
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json",
                                   "User-Agent": "AI-assistant/1.0", "Accept": "application/json"})
        for attempt in range(2):
            try:
                reserve_tokens(key, estimate_tokens(api_messages) + OUTPUT_BUDGET)
                with urlopen(request, timeout=45) as response:
                    content = json.load(response)["choices"][0]["message"]["content"]
                break
            except HTTPError as error:
                if error.code != 429 or attempt:
                    raise
                raw = error.read(4096)
                match = re.search(rb"try again in ([0-9.]+)s", raw)
                try:
                    delay = float(error.headers.get("retry-after", ""))
                except (TypeError, ValueError):
                    delay = float(match.group(1)) if match else 0
                if not 0 < delay <= 30:
                    from io import BytesIO
                    error.fp = BytesIO(raw)
                    raise
                error.close()
                time.sleep(delay + 0.5)
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Groq returned an empty response")
        return content
    try:
        return request_model(chosen)
    except HTTPError as error:
        if selection == "auto" and chosen == "120b" and error.code in {429, 500, 502, 503, 504}:
            return request_model("20b") + "\n\n[Ответ подготовлен на 20B из-за временной недоступности 120B.]"
        # Keep provider diagnostics useful without exposing credentials.
        raw = error.read(4096).decode("utf-8", errors="replace")
        try:
            details = json.loads(raw).get("error", {})
            reason = details.get("message", "") if isinstance(details, dict) else str(details)
        except (ValueError, AttributeError):
            reason = "Cloudflare blocked the API client" if "1010" in raw or "cloudflare" in raw.lower() else "Non-JSON provider response"
        reason = re.sub(r"gsk_[\w-]+", "[redacted]", str(reason).replace(token, "[redacted]"))[:400]
        raise GroqRequestError(error.code, reason) from error
