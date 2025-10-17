from ninja import Schema
from typing import Optional

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
