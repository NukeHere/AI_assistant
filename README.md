# AI Assistant Core

Тестовая база для личного AI-агента. Первый этап максимально простой:
локальное окно на ПК отправляет историю переписки на сервер, сервер передаёт её
в LLM API и возвращает ответ.

## Что уже есть

- Простое desktop-окно на `tkinter`.
- FastAPI server.
- Endpoint `/v1/chat/simple`, который принимает готовую историю сообщений.
- Bearer-token защита клиентских запросов.
- OpenAI-compatible LLM gateway.
- Mock-режим без API-ключа для бесплатной проверки.
- Подготовка к деплою: `Dockerfile`, `render.yaml`, стандартный `PORT`.

Заготовки под persona, память и backup уже лежат в проекте, но они не являются
главным путём первого MVP.

## Быстрый запуск

Вариант без установки зависимостей:

```powershell
$env:APP_API_TOKEN="dev-token"
C:\Ich\projects\AI_assistant\.venv\Scripts\python.exe simple_server.py
```

Во втором терминале:

```powershell
$env:APP_API_TOKEN="dev-token"
C:\Ich\projects\AI_assistant\.venv\Scripts\python.exe desktop_client.py
```

Вариант через FastAPI, когда зависимости установлены:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-fastapi.txt
$env:APP_API_TOKEN="dev-token"
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Проверка:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Сообщение:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/v1/chat/simple `
  -Headers @{ Authorization = "Bearer dev-token" } `
  -ContentType "application/json" `
  -Body '{"messages":[{"role":"system","content":"You are helpful."},{"role":"user","content":"Привет"}]}'
```

## Реальная модель

### Локальная LLaMA через Ollama

Если модель доступна через локальный Ollama API:

```powershell
.\scripts\run_ollama_server.ps1
```

Во втором терминале:

```powershell
.\scripts\run_desktop_ollama.ps1
```

По умолчанию используется порт `8010`, чтобы не конфликтовать с уже запущенным
mock-сервером на `8000`. Если у тебя в Ollama модель называется иначе, поменяй
`MODEL_NAME` перед запуском.

### Ollama Cloud

Для хостинга можно использовать Ollama API token:

```powershell
$env:MODEL_PROVIDER="ollama"
$env:OLLAMA_API_BASE_URL="https://ollama.com"
$env:OLLAMA_API_KEY="..."
$env:MODEL_NAME="gpt-oss:120b"
python simple_server.py
```

Токен не вставляем в код и не отправляем в чат. На хостинге он задаётся только
в переменных окружения.

### OpenAI-compatible API

Если внешний API совместим с `/v1/chat/completions`, достаточно задать:

```powershell
$env:MODEL_API_BASE_URL="https://your-provider.example/v1"
$env:MODEL_API_KEY="..."
$env:MODEL_NAME="your-model"
```

Без `MODEL_PROVIDER` и `MODEL_API_KEY` сервер работает в mock-режиме, чтобы
можно было тестировать архитектуру бесплатно.

## Деплой

Короткая инструкция лежит в [docs/deploy.md](docs/deploy.md).

Важно: бесплатный хостинг не сможет обращаться к локальному Ollama на твоём ПК.
Для облака нужен Ollama API token, внешний OpenAI-compatible API или отдельный
VPS, где модель доступна самому серверу.

