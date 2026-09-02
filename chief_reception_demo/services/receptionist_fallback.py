from __future__ import annotations

import re


PHONE_RE = re.compile(r"(\+?\d[\d\s().-]{4,}\d)")
NAME_PATTERNS = [
    re.compile(r"\b(?:меня зовут|я)\s+([A-Za-zА-Яа-яЁё-]{2,})\b", re.IGNORECASE),
    re.compile(r"\b(?:его|её|ее|её)\s+зовут\s+([A-Za-zА-Яа-яЁё-]{2,})\b", re.IGNORECASE),
    re.compile(r"\b(?:мужа|жену|брата|сестру|сына|дочь|дочку|друга|подругу|девушку)\s+зовут\s+([A-Za-zА-Яа-яЁё-]{2,})\b", re.IGNORECASE),
    re.compile(r"\b(?:моего|мою|моей|моему)\s+(?:мужа|жену|брата|сестру|сына|дочь|дочку|друга|подругу|девушку)\s+зовут\s+([A-Za-zА-Яа-яЁё-]{2,})\b", re.IGNORECASE),
    re.compile(r"\bэто\s+(?:будет\s+)?([A-Za-zА-Яа-яЁё-]{2,})\b", re.IGNORECASE),
    re.compile(r"\bимя\s+([A-Za-zА-Яа-яЁё-]{2,})\b", re.IGNORECASE),
]
BAD_NAME_WORDS = {
    "хуй",
    "хуи",
    "пизда",
    "пиздец",
    "блять",
    "бля",
    "сука",
    "ебать",
    "ёб",
    "еб",
    "мудак",
    "гондон",
}
MIN_NAME_UNIQUE_LETTERS = 2
RANDOM_NAME_FRAGMENTS = ("фыв", "йцу", "ячс", "asdf", "qwer", "zxc", "test", "тест")


def service_aliases() -> dict[str, tuple[str, ...]]:
    return {
        "back_massage": (
            "массаж спины",
            "спина",
            "спины",
            "шея",
            "шеи",
            "поясница",
            "поясницы",
            "болит спина",
            "болит шея",
            "болит поясница",
            "back massage",
            "back",
        ),
        "full_body_massage": (
            "общий массаж",
            "массаж общий",
            "расслабиться",
            "расслабление",
            "усталость",
            "после работы",
            "руки",
            "рук",
            "плечи",
            "плеч",
            "всё тело",
            "все тело",
            "напряжение",
            "full body",
            "body massage",
            "общий",
        ),
        "face_care": ("уход за лицом", "лицо", "facial", "facial care", "уход"),
        "brow_correction": ("брови", "коррекция бровей", "brow", "eyebrow"),
    }


def extract_phone(text: str) -> str | None:
    match = PHONE_RE.search(text)
    if not match:
        return None
    return normalize_phone(match.group(1))


def normalize_phone(value: str) -> str | None:
    raw = value.strip()
    digits = re.sub(r"\D", "", raw)
    if raw.startswith("+") and not raw.startswith("+7"):
        return None
    if len(digits) != 11:
        return None
    if digits.startswith("8"):
        digits = f"7{digits[1:]}"
    if not digits.startswith("7") or digits[1] != "9":
        return None
    return f"+7 {digits[1:4]} {digits[4:7]} {digits[7:9]} {digits[9:11]}"


def extract_name(text: str) -> str | None:
    for pattern in NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()
    return None


def is_probable_name(text: str) -> bool:
    stripped = text.strip()
    return is_valid_client_name(stripped)


def is_valid_client_name(text: str) -> bool:
    stripped = text.strip()
    normalized = stripped.casefold().replace("ё", "е")
    if not re.fullmatch(r"[A-Za-zА-Яа-яЁё-]{2,30}", stripped):
        return False
    if normalized in BAD_NAME_WORDS:
        return False
    if any(word in normalized for word in BAD_NAME_WORDS):
        return False
    if any(fragment in normalized for fragment in RANDOM_NAME_FRAGMENTS):
        return False
    letters = [char for char in normalized if char.isalpha()]
    if len(letters) < 3:
        return normalized in {"ян", "ли", "ан"}
    if len(set(letters)) <= 1 and len(letters) > 2:
        return False
    if len(set(letters)) < MIN_NAME_UNIQUE_LETTERS:
        return False
    consonants = sum(1 for char in letters if char not in "аеёиоуыэюяaeiouy")
    vowels = len(letters) - consonants
    if len(letters) >= 5 and (vowels == 0 or consonants == 0):
        return False
    return True
