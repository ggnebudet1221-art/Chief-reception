from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from chief_reception_demo.database.repositories import ClientQuestionRepository, Service, ServiceRepository
from chief_reception_demo.services.availability_service import AvailabilityService
from chief_reception_demo.services.booking_service import BookingDraft, BookingService
from chief_reception_demo.services.catalog import format_services
from chief_reception_demo.services.extraction_service import ExtractionService
from chief_reception_demo.services.notification_service import NotificationService
from chief_reception_demo.services.receptionist_fallback import (
    extract_name,
    extract_phone,
    is_probable_name,
    is_valid_client_name,
    normalize_phone,
    service_aliases,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramProfile:
    user_id: int
    username: str | None
    first_name: str | None


@dataclass(frozen=True)
class ReceptionistResult:
    reply: str
    booking_created: bool = False


class ReceptionistService:
    def __init__(
        self,
        *,
        services: ServiceRepository,
        availability: AvailabilityService,
        booking_service: BookingService,
        notification_service: NotificationService,
        extraction_service: ExtractionService,
        question_repository: ClientQuestionRepository,
        salon_name: str,
    ) -> None:
        self.services = services
        self.availability = availability
        self.booking_service = booking_service
        self.notification_service = notification_service
        self.extraction_service = extraction_service
        self.question_repository = question_repository
        self.salon_name = salon_name

    async def handle_message(
        self,
        *,
        text: str,
        state_data: dict[str, Any],
        profile: TelegramProfile,
    ) -> tuple[ReceptionistResult, dict[str, Any]]:
        draft = dict(state_data)
        logger.info("[AI] Current client state before message user_id=%s state=%s", profile.user_id, draft)
        extraction = await self.extraction_service.extract(
            message_text=text,
            conversation_state=draft,
            telegram_first_name=profile.first_name,
            telegram_username=profile.username,
        )
        self._merge_fields(draft, extraction.fields)
        self._append_history(draft, role="user", text=text)
        logger.info("[AI] Extracted entities=%s used_ai=%s", extraction.fields, extraction.used_ai)
        logger.info("[AI] Current client state after extraction user_id=%s state=%s", profile.user_id, draft)

        result, next_state = await self._route_next_step(
            text=text,
            draft=draft,
            ai_reply=extraction.reply,
            profile=profile,
        )
        if not result.booking_created:
            self._append_history(next_state, role="assistant", text=result.reply)
        return result, next_state

    async def _route_next_step(
        self,
        *,
        text: str,
        draft: dict[str, Any],
        ai_reply: str | None,
        profile: TelegramProfile,
    ) -> tuple[ReceptionistResult, dict[str, Any]]:
        self._extract_fallback_information(text, draft)
        normalized = text.casefold()
        exact_service = self._detect_exact_service(text)

        if self._is_price_question(normalized):
            logger.info("[AI] Next step reason=price_question")
            return ReceptionistResult(self._price_answer(text, draft)), draft

        if self._is_service_list_question(normalized):
            logger.info("[AI] Next step reason=service_list_question")
            return ReceptionistResult(self._service_list_answer()), draft

        if self._is_working_hours_question(normalized):
            logger.info("[AI] Next step reason=working_hours_question")
            return ReceptionistResult("Мы работаем ежедневно с 10:00 до 20:00."), draft

        if self._is_late_intent(normalized):
            logger.info("[AI] Next step reason=client_late")
            return await self._handle_client_late(text=text, profile=profile)

        if self._is_booking_for_other_intent(normalized):
            logger.info("[AI] Next step reason=booking_for_other")
            draft["booking_for_other"] = True
            draft["other_person_pronoun"] = self._other_person_pronoun(normalized)
            draft["booking_intent"] = True
            target = self._target_pronoun(draft)
            if exact_service:
                draft["selected_service"] = exact_service.title
                draft["awaiting"] = "date"
                return (
                    ReceptionistResult(
                        f"Отличный выбор. {self._selected_service_note(exact_service)} "
                        f"На какой день {target} записать?"
                    ),
                    draft,
                )
            draft["awaiting"] = "service"
            return ReceptionistResult(f"Да, конечно. На какую услугу хотите записать {target}?"), draft

        if self._should_escalate_question(normalized, draft):
            logger.info("[AI] Next step reason=unknown_question_escalated")
            return await self._escalate_client_question(text=text, draft=draft, profile=profile)

        if exact_service and draft.get("awaiting") == "availability_confirmation":
            draft["selected_service"] = exact_service.title
            draft["booking_intent"] = True
            draft.pop("selected_date", None)
            draft.pop("selected_time", None)
            draft.pop("requested_time", None)
            draft["awaiting"] = "date"
            draft.pop("pending_consultation_service", None)
            logger.info("[AI] Next step reason=exact_service_overrode_availability_confirmation")

        early_recommendation = self._consultation_recommendation(text) if not exact_service else None
        if early_recommendation and draft.get("awaiting") != "consultation_confirmation":
            service, reason = early_recommendation
            self._reset_booking_context_for_consultation(draft)
            draft["pending_consultation_service"] = service.title
            draft["booking_intent"] = False
            draft["consultation_explained"] = True
            draft["awaiting"] = "consultation_confirmation"
            logger.info(
                "[AI] Next step reason=consultation_recommendation_overrides_old_context service=%s",
                service.title,
            )
            return ReceptionistResult(f"{reason} Хотите, я запишу вас на него?"), draft

        if draft.get("awaiting") == "availability_confirmation":
            if self._is_positive(normalized):
                draft["awaiting"] = None
                draft["booking_intent"] = True
                logger.info("[AI] Next step reason=availability_confirmed_start_booking")
                next_question = self._next_missing_question(draft)
                return ReceptionistResult(next_question or "Отлично, оформляю запись."), draft
            if self._is_negative(normalized):
                logger.info("[AI] Next step reason=availability_declined")
                return ReceptionistResult("Хорошо. Если захотите подобрать другое время, я помогу."), {}

        if draft.get("awaiting") == "consultation_confirmation":
            if self._is_positive(normalized):
                pending_service = draft.get("pending_consultation_service")
                if pending_service:
                    draft["selected_service"] = pending_service
                draft["booking_intent"] = True
                draft["awaiting"] = "date"
                draft.pop("pending_consultation_service", None)
                logger.info("[AI] Next step reason=consultation_recommendation_confirmed")
                return ReceptionistResult(f"Отлично. На какой день {self._target_pronoun(draft)} записать?"), draft
            if self._is_negative(normalized):
                logger.info("[AI] Next step reason=consultation_recommendation_declined")
                return ReceptionistResult("Хорошо. Если захотите подобрать другой вариант, я помогу."), {}

        if self._is_view_booking_intent(normalized):
            return self._show_latest_booking(profile)

        if draft.get("awaiting") == "cancel_confirmation":
            return await self._handle_cancel_confirmation(normalized, draft)

        if draft.get("awaiting") in {"reschedule_date", "reschedule_time"}:
            return await self._handle_reschedule_step(text=text, draft=draft, profile=profile)

        if self._is_cancel_intent(normalized):
            return await self._start_cancellation(profile=profile)

        if self._is_reschedule_intent(normalized):
            return await self._start_reschedule(text=text, draft=draft, profile=profile)

        if exact_service:
            draft["selected_service"] = exact_service.title
            draft["booking_intent"] = True
            draft.pop("consultation_explained", None)
            draft.pop("pending_consultation_service", None)
            if not draft.get("selected_date"):
                draft["awaiting"] = "date"
                logger.info("[AI] Next step reason=exact_service_selected service=%s", exact_service.title)
                return (
                    ReceptionistResult(
                        f"Отличный выбор. {self._selected_service_note(exact_service)} "
                        f"Подскажите, на какой день {self._target_pronoun(draft)} записать?"
                    ),
                    draft,
                )

        if not draft.get("selected_date"):
            selected_date = self.availability.parse_date(text)
            if selected_date:
                draft["selected_date"] = selected_date
                draft["booking_intent"] = True
                logger.info("[AI] Date found before next-step routing selected_date=%s", selected_date)
        recommendation = self._consultation_recommendation(text)
        if recommendation and not exact_service:
            service, reason = recommendation
            self._reset_booking_context_for_consultation(draft)
            draft["pending_consultation_service"] = service.title
            draft["booking_intent"] = False
            if not draft.get("consultation_explained"):
                draft["consultation_explained"] = True
                draft["awaiting"] = "consultation_confirmation"
                logger.info(
                    "[AI] Next step reason=consultation_recommendation service=%s",
                    service.title,
                )
                return (
                    ReceptionistResult(
                        f"{reason} Хотите, я запишу вас на него?"
                    ),
                    draft,
                )

        if self._is_availability_question(normalized, draft):
            logger.info("[AI] Next step reason=availability_check")
            if (
                draft.get("awaiting") in {"time", "client_name", "phone"}
                and draft.get("booking_intent")
                and draft.get("selected_service")
                and draft.get("selected_date")
            ):
                requested_time = self.availability.parse_requested_time(text)
                if requested_time:
                    draft["requested_time"] = requested_time
                    return self._handle_time_choice_or_check(draft, explicit_check=True), draft
            return self._handle_availability_check(draft), draft

        if draft.get("awaiting") == "time" and draft.get("requested_time"):
            logger.info("[AI] Next step reason=time_selected_from_available_slots")
            return self._handle_time_choice_or_check(draft, explicit_check=False), draft

        if draft.get("invalid_name"):
            logger.info("[AI] Next step reason=invalid_client_name")
            draft["awaiting"] = "client_name"
            draft.pop("invalid_name", None)
            return ReceptionistResult("Пожалуйста, укажите настоящее имя для записи."), draft

        if draft.get("invalid_phone"):
            logger.info("[AI] Next step reason=invalid_phone")
            draft["awaiting"] = "phone"
            draft.pop("invalid_phone", None)
            draft.pop("phone", None)
            return ReceptionistResult("Пожалуйста, укажите номер телефона в формате +7 999 999 99 99."), draft

        if self._wants_services(normalized):
            logger.info("[AI] Next step reason=client_requested_services")
            return ReceptionistResult(format_services(self.services.list_all())), draft

        if self._wants_availability(normalized):
            logger.info("[AI] Next step reason=client_requested_availability")
            return ReceptionistResult(self.availability.format_available_times()), draft

        if not self._has_booking_intent(normalized, draft):
            logger.info("[AI] Next step reason=consultation_without_booking_intent")
            return ReceptionistResult(ai_reply or self._greeting()), draft

        next_question = self._next_missing_question(draft)
        if next_question:
            logger.info("[AI] Next step reason=missing_%s", draft.get("awaiting"))
            return ReceptionistResult(next_question), draft

        logger.info("[AI] Next step reason=all_required_fields_collected")
        return await self._create_booking(draft=draft, profile=profile)

    def _show_latest_booking(self, profile: TelegramProfile) -> tuple[ReceptionistResult, dict[str, Any]]:
        booking = self.booking_service.bookings.get_latest_for_user(profile.user_id)
        if not booking:
            return ReceptionistResult("Я не нашла активную запись на ваше имя."), {}
        date_text = self._format_booking_date_for_client(booking.selected_date)
        return (
            ReceptionistResult(
                f"Вы записаны на {booking.selected_service} {date_text} в {booking.selected_time or ''}. Ждём вас."
            ),
            {},
        )

    def _handle_availability_check(self, draft: dict[str, Any]) -> ReceptionistResult:
        selected_service = draft.get("selected_service")
        selected_date = draft.get("selected_date")
        selected_time = draft.get("selected_time") or draft.get("requested_time")
        if not selected_service:
            draft["awaiting"] = "service"
            return ReceptionistResult("С удовольствием проверю. Какая услуга вас интересует?")
        if not selected_date:
            draft["awaiting"] = "date"
            return ReceptionistResult("С удовольствием проверю. На какой день посмотреть свободные окна?")
        if not selected_time:
            draft["awaiting"] = "time"
            return ReceptionistResult(self._available_times_reply(draft, "На эту дату могу предложить"))

        pretty_date = self._storage_date(str(selected_date))
        is_static_available = self.availability.is_time_available(selected_time)
        is_booked = self.booking_service.bookings.is_slot_booked(
            selected_date=pretty_date,
            selected_time=selected_time,
        )
        if is_static_available and not is_booked:
            draft["awaiting"] = "availability_confirmation"
            draft["booking_intent"] = False
            return ReceptionistResult(
                f"Да, на {pretty_date} в {selected_time} есть свободное место на {selected_service}. "
                "Хотите записаться?"
            )

        if draft.get("selected_time") == selected_time:
            draft.pop("selected_time", None)
        draft.pop("requested_time", None)
        draft["awaiting"] = "time"
        booked_times = self.booking_service.bookings.booked_times_for_date(pretty_date)
        alternatives = [
            time
            for time in self.availability.alternative_times(selected_time, period=draft.get("period"))
            if time not in booked_times
        ]
        if not alternatives:
            alternatives = [
                time
                for time in self.availability.available_times_for_period(draft.get("period"))
                if time not in booked_times
            ][:2]
        return ReceptionistResult(
            f"К сожалению, на {selected_time} мест нет. Свободны {', '.join(alternatives) or 'других окон на эту дату нет'}."
        )

    def _handle_time_choice_or_check(self, draft: dict[str, Any], *, explicit_check: bool) -> ReceptionistResult:
        requested_time = draft.get("requested_time")
        if not requested_time:
            return ReceptionistResult("Подскажите, пожалуйста, какое время вам удобно?")
        pretty_date = self._storage_date(str(draft["selected_date"]))
        booked = self.booking_service.bookings.is_slot_booked(
            selected_date=pretty_date,
            selected_time=requested_time,
        )
        if self.availability.is_time_available(requested_time) and not booked:
            draft["selected_time"] = requested_time
            draft.pop("requested_time", None)
            draft.pop("invalid_name", None)
            next_question = self._next_missing_question(draft)
            suffix = f" {next_question}" if next_question else ""
            if explicit_check:
                return ReceptionistResult(f"Да, {requested_time} свободно. Отличный выбор.{suffix}")
            return ReceptionistResult(f"Отличный выбор.{suffix}")
        if explicit_check or draft.get("selected_time") == requested_time:
            draft.pop("selected_time", None)
        draft.pop("requested_time", None)
        draft["awaiting"] = "time"
        alternatives = [
            time
            for time in self.availability.alternative_times(requested_time, period=draft.get("period"))
            if time not in self.booking_service.bookings.booked_times_for_date(pretty_date)
        ]
        return ReceptionistResult(
            f"К сожалению, {requested_time} занято. Свободны {', '.join(alternatives) or 'других окон на эту дату нет'}."
        )

    async def _create_booking(
        self,
        *,
        draft: dict[str, Any],
        profile: TelegramProfile,
    ) -> tuple[ReceptionistResult, dict[str, Any]]:
        validation_error = self._final_booking_validation(draft)
        if validation_error:
            logger.info("[BOOKING] Final validation stopped booking reason=%s draft=%s", validation_error.reply, draft)
            return validation_error, draft

        logger.info("[BOOKING] Creating booking for user_id=%s draft=%s", profile.user_id, draft)
        booking = self.booking_service.create_booking(
            BookingDraft(
                telegram_user_id=profile.user_id,
                telegram_username=profile.username,
                telegram_first_name=profile.first_name,
                client_name=draft["client_name"],
                phone=draft.get("phone") or None,
                selected_service=draft["selected_service"],
                selected_date=draft["selected_date"],
                selected_time=draft["selected_time"],
            )
        )
        logger.info("[BOOKING] Created booking_id=%s", booking.booking_id)
        logger.info("[NOTIFICATION] Sending owner notification for booking_id=%s", booking.booking_id)
        await self.notification_service.notify_owner(booking)
        logger.info("[NOTIFICATION] Owner notification sent for booking_id=%s", booking.booking_id)
        return ReceptionistResult(reply=self._booking_confirmation(booking), booking_created=True), {}

    def _final_booking_validation(self, draft: dict[str, Any]) -> ReceptionistResult | None:
        if not draft.get("selected_service"):
            draft["awaiting"] = "service"
            return ReceptionistResult("Подскажите, какую услугу записать?")
        if not draft.get("selected_date"):
            draft["awaiting"] = "date"
            return ReceptionistResult("На какой день записать?")
        if not draft.get("selected_time"):
            draft["awaiting"] = "time"
            return ReceptionistResult(self._available_times_reply(draft, "На выбранную дату доступны окна"))
        if not draft.get("client_name") or not is_valid_client_name(str(draft["client_name"])):
            draft["awaiting"] = "client_name"
            draft.pop("client_name", None)
            return ReceptionistResult("Пожалуйста, укажите настоящее имя для записи.")
        phone = normalize_phone(str(draft.get("phone") or ""))
        if not phone:
            draft["awaiting"] = "phone"
            draft.pop("phone", None)
            return ReceptionistResult("Пожалуйста, укажите номер телефона в формате +7 999 999 99 99.")
        draft["phone"] = phone
        pretty_date = self._storage_date(str(draft["selected_date"]))
        selected_time = str(draft["selected_time"])
        booked = self.booking_service.bookings.is_slot_booked(
            selected_date=pretty_date,
            selected_time=selected_time,
        )
        if booked or not self.availability.is_time_available(selected_time):
            draft.pop("selected_time", None)
            draft["awaiting"] = "time"
            booked_times = self.booking_service.bookings.booked_times_for_date(pretty_date)
            alternatives = [
                time
                for time in self.availability.alternative_times(selected_time, period=draft.get("period"))
                if time not in booked_times
            ]
            if not alternatives:
                alternatives = [
                    time
                    for time in self.availability.available_times_for_period(draft.get("period"))
                    if time not in booked_times
                ][:3]
            return ReceptionistResult(
                f"К сожалению, {selected_time} уже занято. Свободны {', '.join(alternatives) or 'других окон на эту дату нет'}."
            )
        draft["selected_date"] = pretty_date
        return None

    async def _start_cancellation(self, *, profile: TelegramProfile) -> tuple[ReceptionistResult, dict[str, Any]]:
        booking = self.booking_service.bookings.get_latest_for_user(profile.user_id)
        if not booking:
            return ReceptionistResult("Я не нашла активную запись для отмены."), {}
        state = {
            "awaiting": "cancel_confirmation",
            "cancel_booking_id": booking.booking_id,
            "telegram_user_id": profile.user_id,
        }
        return (
            ReceptionistResult(
                "Нашла вашу запись:\n\n"
                f"Услуга: {booking.selected_service}\n"
                f"Дата: {booking.selected_date}\n"
                f"Время: {booking.selected_time or ''}\n\n"
                "Подтвердите, пожалуйста, отмену."
            ),
            state,
        )

    async def _handle_cancel_confirmation(
        self,
        normalized: str,
        draft: dict[str, Any],
    ) -> tuple[ReceptionistResult, dict[str, Any]]:
        booking_id = int(draft["cancel_booking_id"])
        booking = self.booking_service.bookings.get_latest_for_user(draft.get("telegram_user_id", -1))
        if not booking or booking.booking_id != booking_id:
            row_booking = None
        else:
            row_booking = booking
        if self._is_negative(normalized):
            return ReceptionistResult("Хорошо, запись оставляю без изменений."), {}
        if not self._is_positive(normalized):
            return ReceptionistResult("Пожалуйста, напишите “да”, если нужно отменить запись."), draft
        if row_booking is None:
            return ReceptionistResult("Не удалось найти запись для отмены. Попробуйте начать заново."), {}
        self.booking_service.bookings.delete(row_booking.booking_id)
        await self.notification_service.notify_owner_cancelled(row_booking)
        return ReceptionistResult("Запись успешно отменена. Будем рады видеть вас снова."), {}

    async def _start_reschedule(
        self,
        *,
        text: str,
        draft: dict[str, Any],
        profile: TelegramProfile,
    ) -> tuple[ReceptionistResult, dict[str, Any]]:
        booking = self.booking_service.bookings.get_latest_for_user(profile.user_id)
        if not booking:
            return ReceptionistResult("Я не нашла активную запись для переноса."), {}
        state = {
            "awaiting": "reschedule_date",
            "reschedule_booking_id": booking.booking_id,
            "telegram_user_id": profile.user_id,
        }
        new_date = self.availability.parse_date(text)
        if new_date:
            state["reschedule_date"] = new_date
            state["awaiting"] = "reschedule_time"
            times = self.availability.available_times_for_period(self.availability.parse_period(text))
            return (
                ReceptionistResult(
                    "Нашла вашу текущую запись:\n\n"
                    f"Услуга: {booking.selected_service}\n"
                    f"Дата: {booking.selected_date}\n"
                    f"Время: {booking.selected_time or ''}\n\n"
                    f"На новую дату доступны окна: {', '.join(times)}. Какое время поставить?"
                ),
                state,
            )
        return (
            ReceptionistResult(
                "Нашла вашу текущую запись:\n\n"
                f"Услуга: {booking.selected_service}\n"
                f"Дата: {booking.selected_date}\n"
                f"Время: {booking.selected_time or ''}\n\n"
                "На какой день перенести запись?"
            ),
            state,
        )

    async def _handle_reschedule_step(
        self,
        *,
        text: str,
        draft: dict[str, Any],
        profile: TelegramProfile,
    ) -> tuple[ReceptionistResult, dict[str, Any]]:
        if draft.get("awaiting") == "reschedule_date":
            new_date = self.availability.parse_date(text)
            if not new_date:
                return ReceptionistResult("Подскажите, пожалуйста, новую дату для переноса."), draft
            draft["reschedule_date"] = new_date
            draft["awaiting"] = "reschedule_time"
            times = self.availability.available_times_for_period(self.availability.parse_period(text))
            return ReceptionistResult(f"На эту дату доступны окна: {', '.join(times)}. Какое время поставить?"), draft

        new_time = self.availability.parse_time(text)
        if not new_time:
            times = self.availability.available_times()
            return ReceptionistResult(f"Выберите, пожалуйста, одно из доступных окон: {', '.join(times)}."), draft
        if not self.availability.is_time_available(new_time):
            alternatives = self.availability.alternative_times(new_time)
            return ReceptionistResult(f"На {new_time} мест нет. Свободны {', '.join(alternatives)}."), draft
        booking_before_update = self.booking_service.bookings.get_by_id(int(draft["reschedule_booking_id"]))
        selected_date = self.availability.format_date(draft["reschedule_date"])
        booked_times = self.booking_service.bookings.booked_times_for_date(selected_date)
        if new_time in booked_times and (
            not booking_before_update
            or booking_before_update.selected_date != selected_date
            or booking_before_update.selected_time != new_time
        ):
            alternatives = [
                time
                for time in self.availability.alternative_times(new_time)
                if time not in booked_times
            ]
            return ReceptionistResult(
                f"К сожалению, {new_time} уже занято. Свободны {', '.join(alternatives) or 'других окон на эту дату нет'}."
            ), draft
        booking = self.booking_service.bookings.update_schedule(
            booking_id=int(draft["reschedule_booking_id"]),
            selected_date=selected_date,
            selected_time=new_time,
        )
        await self.notification_service.notify_owner_rescheduled(booking)
        return (
            ReceptionistResult(
                "Готово, запись перенесена:\n\n"
                f"Услуга: {booking.selected_service}\n"
                f"Дата: {booking.selected_date}\n"
                f"Время: {booking.selected_time or ''}"
            ),
            {},
        )

    async def _handle_client_late(
        self,
        *,
        text: str,
        profile: TelegramProfile,
    ) -> tuple[ReceptionistResult, dict[str, Any]]:
        booking = self.booking_service.bookings.get_latest_for_user(profile.user_id)
        if not booking:
            return ReceptionistResult("Спасибо, что предупредили. Я не нашла активную запись на ваше имя."), {}
        minutes = self._parse_late_minutes(text) or 15
        if not booking.selected_time:
            return ReceptionistResult("Спасибо, что предупредили. Я передам информацию администратору."), {}
        old_time = booking.selected_time
        new_time = self._shift_time(old_time, minutes)
        updated = self.booking_service.bookings.update_schedule(
            booking_id=booking.booking_id,
            selected_date=booking.selected_date,
            selected_time=new_time,
        )
        await self.notification_service.notify_owner_client_late(
            booking=updated,
            old_time=old_time,
            new_time=new_time,
            delay_minutes=minutes,
        )
        return ReceptionistResult(
            "Хорошо, спасибо, что предупредили. Мы скорректировали время записи и будем ждать вас."
        ), {}

    async def _escalate_client_question(
        self,
        *,
        text: str,
        draft: dict[str, Any],
        profile: TelegramProfile,
    ) -> tuple[ReceptionistResult, dict[str, Any]]:
        client_name = draft.get("client_name") or profile.first_name
        question = self.question_repository.create_pending(
            telegram_user_id=profile.user_id,
            telegram_username=profile.username,
            telegram_first_name=profile.first_name,
            client_name=str(client_name) if client_name else None,
            question_text=text,
        )
        logger.info("[AI] Escalated question_id=%s user_id=%s", question.question_id, profile.user_id)
        await self.notification_service.notify_owner_client_question(
            telegram_user_id=profile.user_id,
            client_name=str(client_name) if client_name else None,
            question_text=text,
        )
        return (
            ReceptionistResult("Спасибо за вопрос. Я уточню информацию у администратора и вернусь к вам с ответом."),
            draft,
        )

    def _merge_fields(self, draft: dict[str, Any], fields: dict[str, str]) -> None:
        mapping = {
            "service": "selected_service",
            "date": "selected_date",
            "time": "selected_time",
            "client_name": "client_name",
            "phone": "phone",
            "booking_intent": "booking_intent",
            "period": "period",
            "invalid_name": "invalid_name",
            "invalid_phone": "invalid_phone",
        }
        for source, target in mapping.items():
            value = (fields.get(source) or "").strip()
            if value:
                draft[target] = True if source in {"booking_intent", "invalid_name", "invalid_phone"} else value
        if any(draft.get(key) for key in ("selected_service", "selected_date", "selected_time")):
            draft["booking_intent"] = True

    def _extract_fallback_information(self, text: str, draft: dict[str, Any]) -> None:
        if not text:
            return

        draft["booking_intent"] = draft.get("booking_intent") or self._detect_booking_intent(text)

        phone = extract_phone(text)
        if phone:
            draft["phone"] = phone
        elif draft.get("awaiting") == "phone" and any(char.isdigit() for char in text):
            draft["invalid_phone"] = True

        name = extract_name(text)
        if name:
            if is_valid_client_name(name):
                draft["client_name"] = name
            else:
                draft["invalid_name"] = True

        service = self._detect_service(text)
        if service:
            draft["selected_service"] = service.title

        selected_date = self.availability.parse_date(text, allow_bare_day=draft.get("awaiting") != "time")
        if selected_date:
            draft["selected_date"] = selected_date

        selected_time = self.availability.parse_time(text)
        if selected_time:
            draft["selected_time"] = selected_time
        if self._is_availability_question(text.casefold(), draft) or draft.get("awaiting") == "time":
            requested_time = self.availability.parse_requested_time(text)
            if requested_time:
                draft["requested_time"] = requested_time

        if draft.get("awaiting") == "client_name" and not name:
            if is_probable_name(text):
                draft["client_name"] = text.strip()
            else:
                draft["invalid_name"] = True

        period = self.availability.parse_period(text)
        if period:
            draft["period"] = period

    def _next_missing_question(
        self,
        draft: dict[str, Any],
    ) -> str | None:
        if not draft.get("selected_service"):
            draft["awaiting"] = "service"
            if draft.get("booking_for_other"):
                return f"На какую услугу хотите записать {self._target_pronoun(draft)}?"
            return (
                "Подскажите, пожалуйста, какая услуга вам ближе: массаж спины, "
                "общий массаж, уход за лицом или коррекция бровей?"
            )

        if not draft.get("selected_date"):
            draft["awaiting"] = "date"
            target = self._target_pronoun(draft)
            if draft.get("consultation_explained"):
                return f"На какой день {target} записать?"
            return f"На какой день {target} записать?"

        if not draft.get("selected_time"):
            draft["awaiting"] = "time"
            return self._available_times_reply(draft, "На выбранную дату доступны окна")

        if not draft.get("client_name"):
            draft["awaiting"] = "client_name"
            if draft.get("booking_for_other"):
                return f"Подскажите, как зовут человека, которого записываем?"
            return "Подскажите, как к вам обращаться?"

        if not draft.get("phone"):
            draft["awaiting"] = "phone"
            if draft.get("booking_for_other"):
                return f"Подскажите номер телефона для связи по {self._possessive_target(draft)} записи."
            return "Подскажите номер телефона для связи."

        draft.pop("awaiting", None)
        return None

    def _available_times_reply(self, draft: dict[str, Any], prefix: str) -> str:
        pretty_date = self._storage_date(str(draft.get("selected_date") or ""))
        booked_times = self.booking_service.bookings.booked_times_for_date(pretty_date) if pretty_date else set()
        times = [
            time
            for time in self.availability.available_times_for_period(draft.get("period"))
            if time not in booked_times
        ]
        return f"{prefix}: {', '.join(times) or 'свободных окон нет'}. Какое время {self._dative_target(draft)} удобнее?"

    def _format_booking_date_for_client(self, selected_date: str) -> str:
        try:
            parsed = datetime.strptime(selected_date, "%d.%m.%Y")
        except ValueError:
            return selected_date
        today = self.availability.today()
        if parsed.year == today.year:
            return parsed.strftime("%d.%m")
        return parsed.strftime("%d.%m.%Y")

    def _storage_date(self, selected_date: str) -> str:
        if not selected_date:
            return ""
        try:
            return self.availability.format_date(selected_date)
        except ValueError:
            return selected_date

    def _price_answer(self, text: str, draft: dict[str, Any]) -> str:
        service = self._detect_exact_service(text)
        if not service and draft.get("selected_service"):
            service = self._service_by_title(str(draft["selected_service"]))
        if not service and "массаж" in text.casefold():
            massage_services = [service for service in self.services.list_all() if "массаж" in service.title]
            lines = ["По массажу доступны такие варианты:"]
            for item in massage_services:
                lines.append(f"• {item.title.capitalize()} — {item.price_rub} рублей ({item.duration_minutes} мин)")
            return "\n".join(lines)
        if not service:
            return self._service_list_answer()
        return (
            f"Отличный выбор. {service.title.capitalize()} стоит {service.price_rub} рублей "
            f"и длится {service.duration_minutes} минут. {service.description}"
        )

    def _service_list_answer(self) -> str:
        lines = ["У НАС ДОСТУПНЫ:", ""]
        for service in self.services.list_all():
            lines.append(f"• {service.title.capitalize()} — {service.price_rub} ₽ ({service.duration_minutes} мин)")
        return "\n".join(lines)

    def _service_by_title(self, title: str) -> Service | None:
        normalized = title.casefold()
        for service in self.services.list_all():
            if service.title.casefold() == normalized:
                return service
        return None

    @staticmethod
    def _parse_late_minutes(text: str) -> int | None:
        import re

        match = re.search(r"(\d{1,3})\s*(?:мин|минут)", text.casefold())
        if not match:
            return None
        minutes = int(match.group(1))
        if minutes <= 0:
            return None
        return minutes

    @staticmethod
    def _shift_time(value: str, minutes: int) -> str:
        parsed = datetime.strptime(value, "%H:%M")
        shifted = parsed + timedelta(minutes=minutes)
        return shifted.strftime("%H:%M")

    @staticmethod
    def _other_person_pronoun(text: str) -> str:
        female_markers = ("жену", "дочь", "дочку", "девушку", "сестру", "подругу", "маму")
        male_markers = ("мужа", "брата", "сына", "друга", "папу", "отца")
        if any(marker in text for marker in female_markers):
            return "её"
        if any(marker in text for marker in male_markers):
            return "его"
        return "его"

    @staticmethod
    def _target_pronoun(draft: dict[str, Any]) -> str:
        if draft.get("booking_for_other"):
            return str(draft.get("other_person_pronoun") or "его")
        return "вас"

    @staticmethod
    def _dative_target(draft: dict[str, Any]) -> str:
        if not draft.get("booking_for_other"):
            return "вам"
        return "ей" if draft.get("other_person_pronoun") == "её" else "ему"

    @staticmethod
    def _possessive_target(draft: dict[str, Any]) -> str:
        if not draft.get("booking_for_other"):
            return "вашей"
        return "её" if draft.get("other_person_pronoun") == "её" else "его"

    def _detect_service(self, text: str) -> Service | None:
        normalized = text.casefold()
        for service in self.services.list_all():
            if service.title.casefold() in normalized or service.id.casefold() in normalized:
                return service
            for alias in service_aliases().get(service.id, ()):
                if alias.casefold() in normalized:
                    return service
        return None

    def _detect_exact_service(self, text: str) -> Service | None:
        normalized = text.casefold()
        for service in self.services.list_all():
            if service.title.casefold() in normalized:
                return service
        return None

    @staticmethod
    def _selected_service_note(service: Service) -> str:
        notes = {
            "back_massage": "Массаж спины подойдёт для снятия напряжения в спине и шее.",
            "full_body_massage": "Общий массаж помогает расслабиться и восстановиться после нагрузок.",
            "face_care": "Уход за лицом подойдёт, если хочется освежить кожу и добавить увлажнение.",
            "brow_correction": "Коррекция бровей поможет аккуратно оформить форму.",
        }
        return notes.get(service.id, "Хороший вариант.")

    @staticmethod
    def _reset_booking_context_for_consultation(draft: dict[str, Any]) -> None:
        for key in (
            "selected_service",
            "selected_date",
            "selected_time",
            "requested_time",
            "period",
            "consultation_explained",
            "invalid_name",
            "invalid_phone",
        ):
            draft.pop(key, None)

    def _consultation_recommendation(self, text: str) -> tuple[Service, str] | None:
        normalized = text.casefold()
        rules = [
            (
                ("рук", "плеч", "всё тело", "все тело", "всего тела"),
                "full_body_massage",
                "После нагрузки руки и плечевой пояс часто перенапрягаются. Из доступных услуг лучше подойдёт общий массаж: он помогает расслабить всё тело.",
            ),
            (
                ("расслаб", "устал", "напряж", "после работы", "кайфануть"),
                "full_body_massage",
                "Для расслабления и снятия напряжения лучше всего подойдёт общий массаж. Он помогает расслабить всё тело и восстановиться после нагрузки.",
            ),
            (
                ("шея", "шеи", "поясниц", "спина", "спины"),
                "back_massage",
                "При дискомфорте в спине или шее лучше подойдёт массаж спины: он помогает снять напряжение в этой зоне.",
            ),
            (
                ("лицо", "кожа", "уход", "очищ"),
                "face_care",
                "Если цель связана с состоянием кожи и уходом, лучше подойдет процедура для лица.",
            ),
            (
                ("бров",),
                "brow_correction",
                "Для формы и аккуратного вида бровей подойдет коррекция бровей.",
            ),
        ]
        if not self._is_consultation_request(normalized):
            return None
        for keywords, service_id, reason in rules:
            if any(keyword in normalized for keyword in keywords):
                service = self.services.get(service_id)
                if service:
                    return service, reason
        return None

    def _greeting(self) -> str:
        return (
            f"Здравствуйте. Это {self.salon_name}. Я помогу подобрать услугу, "
            "уточнить свободное время и оформить заявку на запись."
        )

    @staticmethod
    def _booking_confirmation(booking) -> str:
        return (
            "Подтверждаю запись:\n\n"
            f"Услуга: {booking.selected_service}\n"
            f"Дата: {booking.selected_date}\n"
            f"Время: {booking.selected_time or ''}\n"
            f"Имя: {booking.client_name}\n"
            f"Телефон: {booking.phone or ''}\n\n"
            "Запись успешно создана. Мы свяжемся с вами при необходимости."
        )

    @staticmethod
    def _detect_booking_intent(text: str) -> bool:
        normalized = text.casefold()
        return any(
            word in normalized
            for word in (
                "запис",
                "хочу",
                "можно",
                "нужно",
                "нужен",
                "нужна",
                "болит",
                "расслаб",
                "кайф",
                "после работы",
                "после трениров",
            )
        )

    @staticmethod
    def _is_availability_question(text: str, draft: dict[str, Any]) -> bool:
        specific_slot_question = any(
            phrase in text
            for phrase in (
                "есть место",
                "есть окно",
                "есть запись",
                "свободно",
                "свободен",
                "свободна",
                "можно в",
                "можно записаться",
                "получится",
            )
        )
        slot_signal = bool(
            draft.get("selected_date")
            or draft.get("selected_time")
            or draft.get("requested_time")
            or re.search(r"\b\d{1,2}(?::\d{2})?\b", text)
            or any(
                phrase in text
                for phrase in (
                    "сегодня",
                    "завтра",
                    "послезавтра",
                    "после завтра",
                    "через",
                    "выходн",
                    "мест",
                    "окн",
                    "слот",
                    "свобод",
                )
            )
        )
        generic_availability_question = any(phrase in text for phrase in ("есть ли", "есть?")) and slot_signal
        possible_slot_question = "можно" in text and bool(
            draft.get("selected_date") or draft.get("selected_time") or draft.get("requested_time")
        )
        has_slot_context = bool(
            draft.get("selected_service")
            or draft.get("selected_date")
            or draft.get("selected_time")
            or draft.get("requested_time")
        )
        return (specific_slot_question or generic_availability_question or possible_slot_question) and has_slot_context

    @staticmethod
    def _is_cancel_intent(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "отменить запись",
                "отмените запись",
                "я передумал",
                "я передумала",
                "не смогу прийти",
                "не получится прийти",
            )
        )

    @staticmethod
    def _is_reschedule_intent(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "перенести запись",
                "хочу другое время",
                "другое время",
                "можно на другой день",
                "на другой день",
                "перенесите",
            )
        )

    @staticmethod
    def _is_price_question(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "сколько стоит",
                "цена",
                "стоимость",
                "сколько будет стоить",
                "по чем",
                "почём",
            )
        )

    @staticmethod
    def _is_service_list_question(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "какие услуги",
                "что у вас есть",
                "что предлагаете",
                "какие процедуры",
                "покажите прайс",
                "прайс",
                "услуги доступны",
            )
        )

    @staticmethod
    def _is_working_hours_question(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "часы работы",
                "до скольки работаете",
                "во сколько открываетесь",
                "график работы",
                "когда работаете",
            )
        )

    @staticmethod
    def _is_booking_for_other_intent(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "записать жену",
                "записать мужа",
                "записать дочь",
                "записать дочку",
                "записать сына",
                "записать брата",
                "записать сестру",
                "записать девушку",
                "записать друга",
                "записать другого человека",
                "записать подругу",
                "записать маму",
                "записать папу",
            )
        )

    @staticmethod
    def _is_late_intent(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "опоздаю",
                "опоздает",
                "опоздаёт",
                "опаздываю",
                "задержусь",
                "задержится",
                "буду позже",
                "опоздаем",
                "опоздаём",
                "задерживаемся",
            )
        )

    @staticmethod
    def _should_escalate_question(text: str, draft: dict[str, Any]) -> bool:
        if not any(marker in text for marker in ("?", "почему", "можно ли", "а можно", "как ", "где ", "куда ", "кто ", "что ", "когда ")):
            return False
        known_checks = (
            ReceptionistService._is_price_question(text),
            ReceptionistService._is_service_list_question(text),
            ReceptionistService._is_working_hours_question(text),
            ReceptionistService._is_late_intent(text),
            ReceptionistService._is_booking_for_other_intent(text),
            ReceptionistService._is_consultation_request(text),
            ReceptionistService._is_cancel_intent(text),
            ReceptionistService._is_reschedule_intent(text),
            ReceptionistService._is_view_booking_intent(text),
            ReceptionistService._is_availability_question(text, draft),
            ReceptionistService._wants_availability(text),
            ReceptionistService._wants_services(text),
            "запис" in text,
        )
        return not any(known_checks)

    @staticmethod
    def _is_consultation_request(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "что посоветуете",
                "что лучше выбрать",
                "какой массаж выбрать",
                "какой массаж",
                "болит",
                "хочу расслаб",
                "расслабиться",
                "часто",
                "после работы",
                "после трениров",
                "устал",
                "напряж",
                "кайфануть",
            )
        )

    @staticmethod
    def _is_view_booking_intent(text: str) -> bool:
        return any(
            phrase in text
            for phrase in (
                "когда я записан",
                "когда я записана",
                "покажи мою запись",
                "на какое число я записан",
                "на какое число я записана",
                "какая у меня запись",
                "моя запись",
            )
        )

    @staticmethod
    def _is_positive(text: str) -> bool:
        return any(
            word in text
            for word in (
                "да",
                "давайте",
                "запишите",
                "записывайте",
                "подтверждаю",
                "хочу",
                "хочу его",
                "подходит",
                "ок",
                "хорошо",
            )
        )

    @staticmethod
    def _is_negative(text: str) -> bool:
        return any(word in text for word in ("нет", "не надо", "отмена", "передумал", "передумала"))

    @staticmethod
    def _has_booking_intent(text: str, draft: dict[str, Any]) -> bool:
        return bool(draft.get("booking_intent")) or ReceptionistService._detect_booking_intent(text)

    @staticmethod
    def _wants_services(text: str) -> bool:
        return any(word in text for word in ("услуг", "цены", "стоим", "прайс"))

    @staticmethod
    def _wants_availability(text: str) -> bool:
        return any(word in text for word in ("время", "свобод", "окна", "слоты"))

    @staticmethod
    def _append_history(draft: dict[str, Any], *, role: str, text: str) -> None:
        history = list(draft.get("history") or [])
        history.append({"role": role, "text": text})
        draft["history"] = history[-12:]
