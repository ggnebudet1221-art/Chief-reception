from __future__ import annotations

import asyncio
import logging
import random

from telethon import TelegramClient, events

from chief_reception_demo.userbot.claude_dialog import ClaudeDialogClient
from chief_reception_demo.userbot.config import load_userbot_settings
from chief_reception_demo.userbot.conversation_memory import ConversationMemory
from chief_reception_demo.userbot.lead_parser import append_lead_log, extract_booking_lead
from chief_reception_demo.userbot.rahat_prompt import build_system_prompt

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_userbot_settings(".env")

    telegram = TelegramClient(
        settings.telegram_session_name,
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    claude = ClaudeDialogClient(
        api_key=settings.anthropic_api_key,
        base_url=settings.anthropic_base_url,
        model=settings.anthropic_model,
        max_tokens=settings.claude_max_tokens,
    )
    memory = ConversationMemory(max_messages=settings.max_history_messages)
    system_prompt = build_system_prompt()

    @telegram.on(events.NewMessage(incoming=True))
    async def handle_private_message(event: events.NewMessage.Event) -> None:
        if not event.is_private or event.out:
            return
        sender = await event.get_sender()
        sender_id = int(sender.id)
        user_text = (event.raw_text or "").strip()
        if not user_text:
            return

        logger.info("[TELEGRAM] incoming_private sender_id=%s text=%r", sender_id, user_text)
        messages = memory.messages_for_claude(sender_id, user_text)

        async with telegram.action(event.chat_id, "typing"):
            await asyncio.sleep(random.uniform(2, 4))
            ai_text = await claude.complete(system_prompt=system_prompt, messages=messages)

        clean_text, lead = extract_booking_lead(ai_text)
        if not clean_text:
            clean_text = "Спасибо, я уточню информацию и вернусь к вам с ответом."

        memory.append(sender_id, role="user", content=user_text)
        memory.append(sender_id, role="assistant", content=clean_text)

        if lead and not memory.get(sender_id).lead_sent:
            dialog_url = f"tg://user?id={sender_id}"
            append_lead_log(settings.lead_log_path, sender_id=sender_id, lead=lead, dialog_url=dialog_url)
            await telegram.send_message(
                "me",
                "🚨 НОВАЯ ЗАЯВКА НА БРОНЬ:\n"
                f"{lead.notification_text}\n"
                f"Диалог: {dialog_url}",
            )
            memory.mark_lead_sent(sender_id)
            logger.info("[BOOKING] lead_sent sender_id=%s lead=%r", sender_id, lead.raw)
        elif lead:
            logger.info("[BOOKING] duplicate_lead_skipped sender_id=%s", sender_id)

        await event.respond(clean_text)

    logger.info("[TELEGRAM] starting Rahat Float userbot session=%s", settings.telegram_session_name)
    await telegram.start()
    logger.info("[TELEGRAM] Rahat Float userbot is running. Listening to private incoming messages.")
    await telegram.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
