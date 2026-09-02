from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class UserDialogState:
    history: deque[dict[str, str]]
    lead_sent: bool = False


class ConversationMemory:
    def __init__(self, *, max_messages: int) -> None:
        self.max_messages = max_messages
        self._states: dict[int, UserDialogState] = {}

    def get(self, user_id: int) -> UserDialogState:
        if user_id not in self._states:
            self._states[user_id] = UserDialogState(history=deque(maxlen=self.max_messages))
        return self._states[user_id]

    def messages_for_claude(self, user_id: int, user_text: str) -> list[dict[str, str]]:
        state = self.get(user_id)
        messages = list(state.history)
        messages.append({"role": "user", "content": user_text})
        return messages

    def append(self, user_id: int, *, role: str, content: str) -> None:
        self.get(user_id).history.append({"role": role, "content": content})

    def mark_lead_sent(self, user_id: int) -> None:
        self.get(user_id).lead_sent = True
