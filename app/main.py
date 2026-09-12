from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from app.backup import BackupService
from app.config import settings
from app.model_client import ModelClient
from app.personas import (
    Persona,
    build_system_prompt,
    confirmation_for,
    detect_persona_switch,
    extract_alien_glossary_terms,
)
from app.schemas import (
    BackupResponse,
    HealthResponse,
    MessageRequest,
    MessageResponse,
    PersonaRequest,
    SimpleChatRequest,
    SimpleChatResponse,
)
from app.security import require_client_token
from app.storage import Storage

storage = Storage(settings.database_path)
model_client = ModelClient()
backup_service = BackupService(settings.database_path, settings.backup_dir)


@asynccontextmanager
async def lifespan(_: FastAPI):
    storage.init()
    backup_task = asyncio.create_task(backup_service.run_forever())
    try:
        yield
    finally:
        backup_task.cancel()


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        ok=True,
        app=settings.app_name,
        env=settings.app_env,
        model_mode="mock" if model_client.mock_mode else "api",
    )


@app.post(
    "/v1/chat/simple",
    response_model=SimpleChatResponse,
    dependencies=[Depends(require_client_token)],
)
async def simple_chat(request: SimpleChatRequest) -> SimpleChatResponse:
    messages = [message.model_dump() for message in request.messages]
    answer = await model_client.complete(messages)
    return SimpleChatResponse(
        text=answer,
        model_mode="mock" if model_client.mock_mode else "api",
    )


@app.post(
    "/v1/message",
    response_model=MessageResponse,
    dependencies=[Depends(require_client_token)],
)
async def message(request: MessageRequest) -> MessageResponse:
    conversation = storage.ensure_conversation(request.client_id, request.conversation_id)
    active_persona = Persona(conversation["persona"])

    switched_persona = detect_persona_switch(request.text)
    if switched_persona is not None:
        storage.set_persona(request.client_id, request.conversation_id, switched_persona)
        storage.add_message(request.conversation_id, "user", request.text)
        answer = confirmation_for(switched_persona)
        storage.add_message(request.conversation_id, "assistant", answer)
        return MessageResponse(
            conversation_id=request.conversation_id,
            persona=switched_persona,
            switched=True,
            text=answer,
        )

    memories = storage.memories_for_prompt(request.client_id)
    alien_glossary = storage.alien_glossary(request.conversation_id)
    system_prompt = build_system_prompt(active_persona, alien_glossary)
    if memories:
        system_prompt += "\n\nРелевантная долгосрочная память:\n" + "\n".join(f"- {item}" for item in memories)

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(storage.recent_messages(request.conversation_id))
    messages.append({"role": "user", "content": request.text})

    storage.add_message(request.conversation_id, "user", request.text)
    if active_persona == Persona.ALIEN:
        storage.update_alien_glossary(
            request.client_id,
            request.conversation_id,
            extract_alien_glossary_terms(request.text),
        )
    answer = await model_client.complete(messages)
    storage.add_message(request.conversation_id, "assistant", answer)

    return MessageResponse(
        conversation_id=request.conversation_id,
        persona=active_persona,
        switched=False,
        text=answer,
    )


@app.post(
    "/v1/persona",
    response_model=MessageResponse,
    dependencies=[Depends(require_client_token)],
)
async def set_persona(request: PersonaRequest) -> MessageResponse:
    storage.set_persona(request.client_id, request.conversation_id, request.persona)
    answer = confirmation_for(request.persona)
    storage.add_message(request.conversation_id, "assistant", answer)
    return MessageResponse(
        conversation_id=request.conversation_id,
        persona=request.persona,
        switched=True,
        text=answer,
    )


@app.post(
    "/v1/backup/run",
    response_model=BackupResponse,
    dependencies=[Depends(require_client_token)],
)
async def run_backup() -> BackupResponse:
    if not settings.database_path.exists():
        storage.init()
    path = backup_service.run_once()
    return BackupResponse(ok=True, path=str(path), kept=backup_service.kept_count())

