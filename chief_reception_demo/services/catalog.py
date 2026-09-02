from __future__ import annotations

from chief_reception_demo.database.repositories import Service


DEFAULT_SERVICES = [
    Service(
        id="back_massage",
        title="массаж спины",
        price_rub=2500,
        duration_minutes=45,
        description="Расслабление мышц спины и шеи.",
    ),
    Service(
        id="full_body_massage",
        title="общий массаж",
        price_rub=4200,
        duration_minutes=90,
        description="Полный расслабляющий массаж тела.",
    ),
    Service(
        id="face_care",
        title="уход за лицом",
        price_rub=3200,
        duration_minutes=60,
        description="Очищение, маска и увлажняющий уход.",
    ),
    Service(
        id="brow_correction",
        title="коррекция бровей",
        price_rub=1200,
        duration_minutes=30,
        description="Форма и аккуратная укладка.",
    ),
]


def format_services(services: list[Service]) -> str:
    lines = ["Услуги и стоимость:"]
    for index, service in enumerate(services, start=1):
        lines.append(
            f"{index}. {service.title} - {service.price_rub} ₽, {service.duration_minutes} мин"
        )
    return "\n".join(lines)

