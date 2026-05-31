from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.deps import require_token
from src.core.config import get_settings
from src.infrastructure.db.models.memory import UserProfile
from src.infrastructure.db.session import AsyncSessionLocal

router = APIRouter(prefix="/api/profile", tags=["web-profile"], dependencies=[Depends(require_token)])


class ProfileIn(BaseModel):
    text: str


@router.get("")
async def get_profile() -> dict:
    owner = get_settings().web_owner_id
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, owner)
    return {"text": profile.profile_text if profile else ""}


@router.post("")
async def save_profile(payload: ProfileIn) -> dict:
    owner = get_settings().web_owner_id
    txt = payload.text[:500]
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, owner)
        if profile is None:
            profile = UserProfile(user_id=owner, profile_text=txt)
            session.add(profile)
        else:
            profile.profile_text = txt
        await session.commit()
    return {"ok": True}


@router.delete("")
async def clear_profile() -> dict:
    owner = get_settings().web_owner_id
    async with AsyncSessionLocal() as session:
        profile = await session.get(UserProfile, owner)
        if profile:
            await session.delete(profile)
            await session.commit()
    return {"ok": True}
