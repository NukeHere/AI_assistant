$env:APP_API_TOKEN = if ($env:APP_API_TOKEN) { $env:APP_API_TOKEN } else { "dev-token" }
$port = if ($env:ASSISTANT_PORT) { $env:ASSISTANT_PORT } else { "8010" }
$env:ASSISTANT_API_URL = "http://127.0.0.1:$port/v1/chat/simple"
.\.venv\Scripts\pythonw.exe desktop_client.py
