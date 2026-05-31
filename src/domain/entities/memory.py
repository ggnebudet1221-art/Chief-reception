from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True)
class ConversationEntity:
    id: int
    telegram_user_id: int
    title: str
    created_at: datetime


@dataclass(slots=True)
class MessageEntity:
    id: int
    conversation_id: int
    role: str
    content: str
    created_at: datetime
