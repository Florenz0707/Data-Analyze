from typing import List, Optional

from ninja import Schema


class LoginIn(Schema):
    username: str
    password: str


class ChatIn(Schema):
    session_id: str = "default_session"
    user_input: str
    use_history: Optional[str] = None


class ChatOut(Schema):
    reply: str


class HistoryOut(Schema):
    history: str


class ErrorResponse(Schema):
    error: str


class ProvidersOut(Schema):
    providers: List[str]


class LocalModelsOut(Schema):
    transformers: List[str]
    ollama: List[str]


class SelectLLMIn(Schema):
    provider: str
    model: Optional[str] = None


class SelectLLMOut(Schema):
    provider: str
    model: Optional[str] = None


class SessionIn(Schema):
    session_id: str = "default_session"


class SessionOut(Schema):
    session_id: str


class SessionListOut(Schema):
    sessions: List[str]


# ===== External API Schemas =====
class APIIn(Schema):
    base_url: str
    model_name: str
    api_key: str
    alias: Optional[str] = None


class ModelsListOut(Schema):
    models_list: List[str]


class ModelIn(Schema):
    model_name: str
