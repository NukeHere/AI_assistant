# AI Assistant Core

Тестовая база для личного AI-агента. Первый этап: desktop- и Android-клиенты
отправляют сообщения на сервер, сервер хранит контекст разговора, выбирает
активный режим поведения, добавляет память в prompt, передаёт запрос в LLM API и
возвращает ответ.

## Что уже есть

- Простое desktop-окно на `tkinter`.
- Минимальный Android-клиент в `android_client`.
- Сервер для Render с лёгким ядром на stdlib; загрузка Telegram-видео использует yt-dlp, Deno и FFmpeg.
- FastAPI server как расширяемый каркас для следующих этапов.
- Endpoint `/v1/message`, который хранит контекст разговора на сервере.
- Endpoint `/v1/history`, который отдаёт историю разговора для клиента.
- Legacy endpoint `/v1/chat/simple`, который принимает готовую историю сообщений.
- Bearer-token защита клиентских запросов.
- OpenAI-compatible и Ollama LLM gateway.
- Mock-режим без API-ключа для бесплатной проверки.
- Три режима: дружелюбный `FRIEND` по умолчанию, прежние `ANA` и `ALIEN`; явное переключение через `/friend`, `/ana`, `/alien` или просьбу в чате.
- Сохранение режима и истории разговора между сообщениями.
- Локальная история desktop-клиента после перезапуска приложения.
- Ручная память через `запомни: ...`, `/remember ...` и просмотр через `/memory`.
- Умная память: модель может сама сохранять важные/тематические ячейки через private memory directives.
- Каждая ячейка памяти хранит краткое `summary`, полное `full_content`, темы и локальный embedding для поиска.
- Устойчивый словарь метафор ALIEN внутри разговора.
- UI-оформление structured-блоков `Observation`, `Diagnosis`, `Action`, `Explanation`, `Conclusion`.
- Команда `/functions` и системный список возможностей, чтобы модель знала свои доступные функции и ограничения.
- Snapshot памяти/истории через `/v1/snapshot`: desktop-клиент хранит локальную копию и восстанавливает Render после redeploy/restart.
- Синхронизация истории между устройствами через `/v1/history` и `/v1/sync`: desktop и Android подтягивают серверный чат по выбранному `client_id`/`conversation_id`. Новые установки создают постоянный уникальный `client_id` на устройстве; его можно оставить отдельным для тестера или вручную заменить на существующий ID профиля.
- Очистка Markdown/structured-разметки в клиентах: `**...**`, code ticks, заголовки и служебные labels отображаются аккуратнее.
- Telegram-интерфейс как дополнительный канал общения: guest mode, `/link <client_id>`, `/whoami`, secretary-mode фильтрация шума.
- Telegram-сводка: сервер собирает сообщения участников группы, отправляет короткую шутливую историю дня в 23:00 по Москве и отвечает на `/summary` или просьбу о сводке за текущий день. Сводка связывает главные сцены в сюжет и упоминает каждого активного участника. Бесплатный Render может спать; сводка отправится после пробуждения. Для сохранения накопленных сообщений на Render нужна Turso.
- Администраторы Telegram-группы могут остановить обычные ответы бота командой `/quiet` или фразой «бот, замолчи» и снова включить их через `/speak` или «бот, говори». Состояние хранится отдельно для каждой группы. В тишине бот отвечает на явное `@VBDsThirdSon_bot` и на ответ на своё сообщение: так можно запросить сводку или попросить выполнить другое доступное действие, включая модерацию. Обычные сообщения он пропускает, но продолжает публиковать ежедневную сводку по расписанию; личные сообщения тоже работают. При первой управляющей команде бот сохраняет список администраторов группы, а перед каждым переключением проверяет статус отправителя через Telegram. `/admins` показывает сохранённый список администраторам.
- Создатель бота — `@Ne_gr_idi_rabotai` (Telegram ID `1834373176`). Сервер не выдаёт ему мут даже при смене ника. `TG_CREATOR_USER_ID` позволяет дополнительно защитить другой ID, если потребуется.
- Аудит-логи `/v1/logs` и `data/assistant-audit.jsonl`: входящие сообщения, routing/ignore-решения и счётчики памяти без секретов.
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

## Постоянное хранение на Render через Turso

По умолчанию сервер использует локальный SQLite, который на бесплатном Render не переживает остановку или перезапуск сервиса. Чтобы хранить историю, память, стикеры, сводки и режим тишины постоянно:

1. Создай базу в Turso. Поддерживаются оба типа: Turso Database (`turso://...`) и libSQL (`libsql://...`). Для Turso Database при использовании CLI команда: `turso db create ai-assistant --tursodb`; без флага создаётся libSQL.
2. Получи URL базы и токен доступа в Turso. Не публикуй токен в чате или Git.
3. Добавь в Render Environment переменные `TURSO_DATABASE_URL` и `TURSO_AUTH_TOKEN`. Скопируй URL базы целиком, включая префикс `turso://` или `libsql://`; токен укажи как secret.
4. После перезапуска проверь `/health`: поле `storage_backend` должно быть `turso` либо `libsql` в зависимости от URL.

Оба параметра обязательны одновременно: неполная конфигурация останавливает запуск, чтобы сервер случайно не начал писать в временный SQLite. Пустая база создаёт таблицы автоматически. Старые данные из локальной SQLite сами в Turso не переносятся; при необходимости сначала восстанови их из сохранённого snapshot/резервной копии. Сон бесплатного Render всё равно может задержать сводку и напоминания.

Сервер держит в оперативной памяти последние сообщения группы, ожидающий и сжатый контекст, настройки чата и список стикеров. Кэш ограничен 128 записями и обновляется при изменениях; через 60 секунд данные перечитываются из Turso. После сна Render кэш пустой, а постоянные данные остаются в Turso.

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


## Telegram

Telegram-бот подключается как дополнительный интерфейс к тому же серверу, памяти и синхронизации. Подробности — в [docs/telegram.md](docs/telegram.md).

Быстрый сценарий:

1. В desktop или Android посмотри поле `Client ID`, нажми `Копировать ID`/`ID` или отправь `/id`.
2. В Telegram напиши боту `/link <client_id>`.
3. После привязки сообщения из Telegram идут в тот же `client_id` и синхронизируются с основными приложениями. Для отдельного тестера оставь его сгенерированный ID; для одного общего профиля вставь одинаковый ID на нескольких устройствах.

На Render нужно задать `TG_BOT_API_KEY`; для защищённого webhook желательно также `TG_WEBHOOK_SECRET`. Чтобы получать реакции, при настройке webhook включи `allowed_updates` со значениями `message`, `edited_message` и `message_reaction`.

Автоматическая загрузка видео по ссылкам YouTube, Shorts, Instagram и TikTok сейчас отключена: `TG_VIDEO_DOWNLOAD_ENABLED=false` по умолчанию. Бот не ставит такие ссылки в очередь и не показывает статус загрузки. Код загрузчика сохранён для повторной проверки после появления рабочего доступа к источникам. При включении бот сначала выбирает совместимый MP4 до 45 МБ; при необходимости скачивает ограниченный по свободному месту исходник до 240 МБ и преобразует его через FFmpeg в MP4 до 45 МБ. На облачном Bot API Telegram предел новой загрузки составляет 50 МБ.

При включённой загрузке бот показывает «Скачиваю 🎬», а после результата удаляет этот статус. Сообщения других ботов без обращения к помощнику не запускают ответ модели. YouTube на Render возвращал проверку «Sign in to confirm you’re not a bot» даже после запуска PO Token provider. При необходимости можно задать прокси через `YT_DLP_PROXY_URL` или cookies через `YT_DLP_COOKIES_B64` в Render Environment. Второе значение — base64 от файла cookies в формате Netscape; оно считается секретом и не должно попадать в Git.

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


## Резервные копии памяти Render на ПК

На бесплатном Render файловая система не постоянная: после redeploy/restart SQLite-память может очиститься. Для MVP добавлен локальный backup-agent, который запускается на твоём ПК и забирает snapshot памяти с Render.

Один backup вручную:

```powershell
.\.venv\Scripts\python.exe backup_agent.py once
```

Тестовый постоянный режим: backup каждые 10 секунд, хранить последние 10 копий:

```powershell
.\.venv\Scripts\python.exe backup_agent.py loop
```

Восстановить Render из последней локальной копии:

```powershell
.\.venv\Scripts\python.exe backup_agent.py restore latest
```

Копии лежат по умолчанию в `%USERPROFILE%\.ai_assistant\backups`. Если хочешь хранить их в Яндекс.Диске, добавь в локальный `.env` переменную `ASSISTANT_BACKUP_DIR` с путём к синхронизируемой папке.


## Sync

Desktop and Android clients now use append-only message sync through /v1/sync; see docs/sync.md.

## Timed memory

The server exposes current time to the model and supports scheduled timed-memory reminders; see docs/timed_memory.md.
