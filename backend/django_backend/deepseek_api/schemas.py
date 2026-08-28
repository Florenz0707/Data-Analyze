from ninja import Schema


class LoginIn(Schema):
    username: str
    password: str


class ChatIn(Schema):
    session_id: str = "default_session"
    user_input: str
    use_history: str | None = None


class ChatOut(Schema):
    reply: str


class HistoryItem(Schema):
    user_input: str
    response: str


class HistoryListOut(Schema):
    turns: list[HistoryItem]


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


class SessionOut(Schema):
    session_id: str


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
