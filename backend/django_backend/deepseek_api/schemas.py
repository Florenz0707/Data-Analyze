from typing import List, Optional

from ninja import Schema


class LoginIn(Schema):
    username: str
    password: str


class LoginOut(Schema):
    api_key: str
    expiry: int


class ChatIn(Schema):
    session_id: str = "default_session"
    user_input: str


class ChatOut(Schema):
    reply: str


class HistoryOut(Schema):
    history: str


class ErrorResponse(Schema):
    error: str


# LLM provider/model 相关的入参/出参结构（paramIn/paramOut）
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
