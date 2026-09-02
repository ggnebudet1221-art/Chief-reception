from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from src.core.config import get_settings
from src.infrastructure.logging.logger import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class AgentDefinition:
    id: str
    name: str
    role: str
    bot_token: str
    telegram_chat_id: int
    system_prompt: str


def _read_prompt(agent_id: str, path: str) -> str:
    prompt_path = Path(path)
    if not prompt_path.is_absolute():
        prompt_path = Path.cwd() / prompt_path
    try:
        text = prompt_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        logger.error("Agent prompt file is missing", agent=agent_id, path=str(prompt_path), error=str(error))
        raise RuntimeError(f"Agent prompt file is missing for {agent_id}: {prompt_path}") from error
    if not text:
        logger.error("Agent prompt file is empty", agent=agent_id, path=str(prompt_path))
        raise RuntimeError(f"Agent prompt file is empty for {agent_id}: {prompt_path}")
    return text


class AgentRegistry:
    def __init__(self) -> None:
        settings = get_settings()
        self._agents = {
            "chief": AgentDefinition(
                id="chief",
                name=settings.chief_agent_name,
                role="orchestrator",
                bot_token=settings.chief_bot_token,
                telegram_chat_id=settings.telegram_chief_chat_id,
                system_prompt=_read_prompt("chief", settings.chief_prompt_path),
            ),
            "business": AgentDefinition(
                id="business",
                name=settings.business_agent_name,
                role="business strategist",
                bot_token=settings.business_bot_token,
                telegram_chat_id=settings.telegram_business_chat_id,
                system_prompt=_read_prompt("business", settings.business_prompt_path),
            ),
            "smm": AgentDefinition(
                id="smm",
                name=settings.smm_agent_name,
                role="content and social media strategist",
                bot_token=settings.smm_bot_token,
                telegram_chat_id=settings.telegram_smm_chat_id,
                system_prompt=_read_prompt("smm", settings.smm_prompt_path),
            ),
        }
        self.coordination_chat_id = settings.telegram_coordination_chat_id

    def all(self) -> list[AgentDefinition]:
        return list(self._agents.values())

    def get(self, agent_id: str) -> AgentDefinition:
        return self._agents.get(agent_id, self._agents["chief"])

    def enabled_bots(self) -> list[AgentDefinition]:
        return [agent for agent in self.all() if agent.bot_token]

    def by_chat_id(self, chat_id: int) -> AgentDefinition | None:
        for agent in self._agents.values():
            if agent.telegram_chat_id and agent.telegram_chat_id == chat_id:
                return agent
        return None

    def route(self, text: str, chat_id: int = 0, source_agent_id: str = "") -> AgentDefinition:
        if source_agent_id and source_agent_id != "chief" and source_agent_id in self._agents:
            agent = self.get(source_agent_id)
            logger.info("agent selected", selected_agent=agent.id, reason="source_agent")
            return agent

        direct = self.by_chat_id(chat_id)
        if direct:
            logger.info("agent selected", selected_agent=direct.id, reason="direct_agent_chat")
            return direct

        clean = (text or "").strip()
        for agent_id, aliases in {
            "business": ("business", "бизнес"),
            "smm": ("smm", "смм"),
            "chief": ("chief", "чиф"),
        }.items():
            for alias in aliases:
                if re.match(rf"^\s*{re.escape(alias)}\s*[,.:;\-?]\s+", clean, flags=re.IGNORECASE):
                    agent = self.get(agent_id)
                    logger.info("agent selected", selected_agent=agent.id, reason="explicit_text_trigger")
                    return agent

        agent = self.get("chief")
        logger.info("agent selected", selected_agent=agent.id, reason="general_assistant")
        return agent
