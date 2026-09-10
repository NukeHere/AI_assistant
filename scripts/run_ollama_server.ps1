$env:APP_API_TOKEN = if ($env:APP_API_TOKEN) { $env:APP_API_TOKEN } else { "dev-token" }
$env:MODEL_PROVIDER = "ollama"
$env:MODEL_NAME = if ($env:MODEL_NAME) { $env:MODEL_NAME } else { "gpt-oss:120b-cloud" }
$env:ASSISTANT_PORT = if ($env:ASSISTANT_PORT) { $env:ASSISTANT_PORT } else { "8010" }
.\.venv\Scripts\python.exe simple_server.py
