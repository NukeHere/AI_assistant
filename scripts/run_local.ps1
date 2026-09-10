$env:APP_API_TOKEN = if ($env:APP_API_TOKEN) { $env:APP_API_TOKEN } else { "dev-token" }
$env:DATABASE_PATH = if ($env:DATABASE_PATH) { $env:DATABASE_PATH } else { "./data/assistant.sqlite3" }
.\.venv\Scripts\python.exe simple_server.py
