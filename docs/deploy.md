# Деплой первого MVP

## Важное ограничение

Бесплатный облачный сервер не сможет обратиться к Ollama на локальном компьютере
по `127.0.0.1:11434`. Для облака нужен внешний API. Если есть Ollama API token,
используем прямой доступ к Ollama Cloud.

Локальный режим:

```powershell
MODEL_PROVIDER=ollama
OLLAMA_API_BASE_URL=http://127.0.0.1:11434
```

Облачный режим:

```powershell
MODEL_PROVIDER=ollama
OLLAMA_API_BASE_URL=https://ollama.com
OLLAMA_API_KEY=...
MODEL_NAME=gpt-oss:120b
APP_API_TOKEN=long-random-client-token
```

## Render

Проект уже содержит `render.yaml`. Для деплоя через Render нужно:

1. Залить проект в GitHub.
2. Создать Web Service из репозитория.
3. Указать переменные `OLLAMA_API_KEY`, `MODEL_NAME`.
4. Скопировать сгенерированный `APP_API_TOKEN` в настройки desktop-клиента.

Start command:

```bash
python simple_server.py
```

Health check:

```text
/health
```

## Российский VPS

Если выбран Timeweb, Selectel, Yandex Cloud или VK Cloud, проще всего запускать
тот же `simple_server.py` как обычный Python-процесс за nginx/HTTPS.

Минимальные переменные:

```powershell
$env:ASSISTANT_HOST="0.0.0.0"
$env:ASSISTANT_PORT="8000"
$env:MODEL_PROVIDER="ollama"
$env:OLLAMA_API_BASE_URL="https://ollama.com"
$env:OLLAMA_API_KEY="..."
$env:MODEL_NAME="gpt-oss:120b"
$env:APP_API_TOKEN="long-random-client-token"
python simple_server.py
```

Note: requirements.txt intentionally does not install packages for the first MVP. The deployed simple_server.py uses only Python standard library modules.
