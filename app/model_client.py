from __future__ import annotations

import httpx

from app.config import settings


class ModelClient:
    def __init__(self) -> None:
        self.mock_mode = not bool(settings.model_api_key)

    async def complete(self, messages: list[dict[str, str]]) -> str:
        if self.mock_mode:
            return self._mock_response(messages)

        url = settings.model_api_base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": settings.model_name,
            "messages": messages,
            "temperature": 0.7,
        }
        headers = {
            "Authorization": f"Bearer {settings.model_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=settings.model_timeout_seconds) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    def _mock_response(self, messages: list[dict[str, str]]) -> str:
        system = messages[0]["content"] if messages else ""
        user_text = ""
        for message in reversed(messages):
            if message["role"] == "user":
                user_text = message["content"]
                break

        if "Активная личность: ALIEN" in system:
            return (
                "Окно приняло песню. "
                f"Тестовый ответ без внешней модели: {user_text[:180]}"
            )
        return (
            "Запрос принят. "
            f"Тестовый ответ без внешней модели: {user_text[:180]}"
        )

