from datetime import datetime
from uuid import UUID

from ninja import Schema


class LoginIn(Schema):
    username: str
    password: str


class ChatIn(Schema):
    session_id: str = "default_session"
    user_input: str
    use_history: str | None = None
    message_id: UUID | None = None


class ChatOut(Schema):
    reply: str


class HistoryItem(Schema):
    id: int
    sequence: int
    message_id: UUID
    created_at: datetime
    user_input: str
    response: str


class HistoryListOut(Schema):
    turns: list[HistoryItem]
    next_before_id: int | None = None
    next_after_id: int | None = None
    next_before_cursor: str | None = None
    next_after_cursor: str | None = None
    has_more_before: bool = False
    has_more_after: bool = False


class ErrorResponse(Schema):
    error: str


class ProvidersOut(Schema):
    providers: list[str]


class LocalModelsOut(Schema):
    transformers: list[str]
    ollama: list[str]


class SelectLLMIn(Schema):
    provider: str
    model: str | None = None


class SelectLLMOut(Schema):
    provider: str
    model: str | None = None


class SessionIn(Schema):
    session_id: str = "default_session"
    title: str | None = None


class SessionOut(Schema):
    session_id: str
    title: str = ""


class SessionListOut(Schema):
    sessions: list[str]


class APIIn(Schema):
    base_url: str
    model_name: str
    api_key: str
    alias: str | None = None


class ModelsListOut(Schema):
    models_list: list[str]


class ModelIn(Schema):
    model_name: str
