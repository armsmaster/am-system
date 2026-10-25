from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from am_common.auth import CurrentPrincipal, Role

router = APIRouter()


class MeResponse(BaseModel):
    subject: str
    username: str
    roles: list[Role]


@router.get("/me", tags=["me"])
async def me(principal: CurrentPrincipal) -> MeResponse:
    """Кто я: пользователь и роли из токена.

    Позже здесь же появится список доступных пользователю портфелей.
    """
    return MeResponse(
        subject=principal.subject,
        username=principal.username,
        roles=sorted(principal.roles),
    )
