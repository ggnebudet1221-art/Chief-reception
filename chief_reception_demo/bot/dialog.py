from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from chief_reception_demo.services.receptionist_service import ReceptionistService, TelegramProfile


def create_router(*, receptionist: ReceptionistService) -> Router:
    router = Router(name="reception_demo")

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer(
            "Здравствуйте. Я администратор салона и помогу оформить запись. "
            "Напишите, какая услуга вас интересует и на какой день вам удобно."
        )

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        await message.answer("Запись отменена. Когда будете готовы, напишите, на какую услугу хотите записаться.")

    @router.message(Command("services"))
    async def services(message: Message, state: FSMContext) -> None:
        await handle_text("покажите услуги и цены", message, state)

    @router.message(Command("prices"))
    async def prices(message: Message, state: FSMContext) -> None:
        await handle_text("покажите цены", message, state)

    @router.message(Command("times"))
    async def times(message: Message, state: FSMContext) -> None:
        await handle_text("покажите свободное время", message, state)

    @router.message(Command("book"))
    async def book(message: Message, state: FSMContext) -> None:
        await handle_text("хочу записаться", message, state)

    @router.message(F.text)
    async def free_text(message: Message, state: FSMContext) -> None:
        await handle_text(message.text or "", message, state)

    async def handle_text(text: str, message: Message, state: FSMContext) -> None:
        profile = TelegramProfile(
            user_id=message.from_user.id,
            username=message.from_user.username,
            first_name=message.from_user.first_name,
        )
        result, new_state = await receptionist.handle_message(
            text=text,
            state_data=await state.get_data(),
            profile=profile,
        )
        if result.booking_created:
            await state.clear()
        else:
            await state.set_data(new_state)
        await message.answer(result.reply)

    return router
