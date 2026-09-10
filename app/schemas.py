from __future__ import annotations

from pydantic import BaseModel, Field

from app.personas import Persona


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(min_length=1, max_length=20000)


class SimpleChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1, max_length=100)


class SimpleChatResponse(BaseModel):
    text: str
    model_mode: str


class MessageRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(default="default", min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=20000)
    input_type: str = Field(default="text", pattern="^(text|voice_transcript)$")


class MessageResponse(BaseModel):
    conversation_id: str
    persona: Persona
    switched: bool
    text: str


class PersonaRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(default="default", min_length=1, max_length=128)
    persona: Persona


class HealthResponse(BaseModel):
    ok: bool
    app: str
    env: str
    model_mode: str


class BackupResponse(BaseModel):
    ok: bool
    path: str
    kept: int
