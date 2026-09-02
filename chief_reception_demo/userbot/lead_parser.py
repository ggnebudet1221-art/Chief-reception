from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


BOOKING_LEAD_RE = re.compile(r"\[BOOKING_LEAD:\s*(.*?)\]", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class BookingLead:
    raw: str

    @property
    def notification_text(self) -> str:
        return self.raw.strip()


def extract_booking_lead(text: str) -> tuple[str, BookingLead | None]:
    match = BOOKING_LEAD_RE.search(text)
    if not match:
        return text.strip(), None
    clean_text = BOOKING_LEAD_RE.sub("", text).strip()
    return clean_text, BookingLead(raw=match.group(1).strip())


def append_lead_log(path: Path, *, sender_id: int, lead: BookingLead, dialog_url: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "sender_id": sender_id,
        "lead": lead.raw,
        "dialog_url": dialog_url,
    }
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False) + "\n")
