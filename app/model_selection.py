"""Owner-controlled persistent Groq model routing."""
import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


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
    token = os.getenv("GROQ_API_KEY") or os.getenv("MODEL_API_KEY", "")
    if not token:
        raise RuntimeError("GROQ_API_KEY is required")
    selection = storage.model_selection()
    chosen = choose_model(selection, messages)
    def request_model(key):
        payload = {"model": models()[key]["id"], "messages": messages, "temperature": 0.7}
        request = Request("https://api.groq.com/openai/v1/chat/completions",
                          data=json.dumps(payload).encode(),
                          headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
        with urlopen(request, timeout=45) as response:
            content = json.load(response)["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Groq returned an empty response")
        return content
    try:
        return request_model(chosen)
    except HTTPError as error:
        if selection == "auto" and chosen == "120b" and error.code in {429, 500, 502, 503, 504}:
            return request_model("20b") + "\n\n[Ответ подготовлен на 20B из-за временной недоступности 120B.]"
        raise
