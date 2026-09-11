# AI Assistant Core

Тестовая база для личного AI-агента. Первый этап: desktop- и Android-клиенты
отправляют сообщения на сервер, сервер хранит контекст разговора, выбирает
активный режим поведения, добавляет память в prompt, передаёт запрос в LLM API и
возвращает ответ.

## Что уже есть

- Простое desktop-окно на `tkinter`.
- Минимальный Android-клиент в `android_client`.
- Лёгкий stdlib-сервер для Render без тяжёлых зависимостей.
- FastAPI server как расширяемый каркас для следующих этапов.
- Endpoint `/v1/message`, который хранит контекст разговора на сервере.
- Endpoint `/v1/history`, который отдаёт историю разговора для клиента.
- Legacy endpoint `/v1/chat/simple`, который принимает готовую историю сообщений.
- Bearer-token защита клиентских запросов.
- OpenAI-compatible и Ollama LLM gateway.
- Mock-режим без API-ключа для бесплатной проверки.
- Режимы `ANA` и `ALIEN` с переключением через `/ana`, `/alien` и естественные фразы.
- Сохранение режима и истории разговора между сообщениями.
- Локальная история desktop-клиента после перезапуска приложения.
- Простая память через `запомни: ...`, `/remember ...` и просмотр через `/memory`.
- Устойчивый словарь метафор ALIEN внутри разговора.
- UI-оформление structured-блоков `Observation`, `Diagnosis`, `Action`, `Explanation`, `Conclusion`.
- Заготовки под backup и следующий голосовой слой.
- Подготовка к деплою: `Dockerfile`, `render.yaml`, стандартный `PORT`.

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

Desktop-клиент читает локальный `.env`, если он лежит рядом с `desktop_client.py`.
Файл `.env` игнорируется Git и не должен попадать в GitHub.

## API-проверка

Health:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

Сообщение:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/v1/message `
  -Headers @{ Authorization = "Bearer dev-token" } `
  -ContentType "application/json" `
  -Body '{"client_id":"primary-user","conversation_id":"default","text":"Привет"}'
```

Память:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/v1/message `
  -Headers @{ Authorization = "Bearer dev-token" } `
  -ContentType "application/json" `
  -Body '{"client_id":"primary-user","conversation_id":"default","text":"запомни: мой основной проект называется AI Assistant"}'
```

Переключение режима:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/v1/message `
  -Headers @{ Authorization = "Bearer dev-token" } `
  -ContentType "application/json" `
  -Body '{"client_id":"primary-user","conversation_id":"default","text":"/alien"}'
```

## Android

Android Studio-проект лежит в [android_client](android_client). Подробности — в
[android_client/README.md](android_client/README.md).

Токен в Android-клиент вводится руками в поле `APP_API_TOKEN`. В коде его нет.

## Голосовой режим

Идеи и порядок реализации лежат в [docs/voice_mode.md](docs/voice_mode.md).
Самый разумный следующий шаг: push-to-talk на Android, где телефон распознаёт
голос в текст, отправляет обычный `/v1/message`, а затем читает ответ через TTS.

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

Важно: бесплатный Render без persistent disk не является надёжным долговременным
хранилищем. Для настоящей памяти между redeploy/restart позже нужен внешний
storage: PostgreSQL, S3/Yandex Disk backup-слой или отдельный VPS с диском.
