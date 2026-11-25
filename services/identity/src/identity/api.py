"""HTTP API сервиса входа.

``/public/...`` — без токена: вход, обновление сессии, выход, первый вход с
временным паролем, приглашение. Остальное — с токеном: смена своего пароля и,
для администратора, пользователи.

Access token браузер держит в памяти. Refresh token — в cookie: HttpOnly
(скрипты страницы его не видят), SameSite=Strict (чужой сайт его не пошлёт),
путь — только адреса входа. С «Запомнить меня» cookie живёт до 30 дней и
переживает закрытие браузера; без него — до закрытия браузера.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Annotated, Any, cast

import jwt
from fastapi import APIRouter, Cookie, Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncSession

from am_common.app import AppResources, get_session
from am_common.auth import CurrentPrincipal, Principal, Role, require_any_role
from am_common.events import Actor, Event
from am_common.outbox import Outbox
from identity import repository
from identity.db import outbox as outbox_table
from identity.domain import (
    HUMAN_ROLES,
    HumanRole,
    InviteTtl,
    UserStatus,
    check_invite,
    check_password,
    user_status,
)
from identity.keycloak import (
    IdentityError,
    KeycloakAdmin,
    KeycloakAuth,
    KeycloakUnavailableError,
    Tokens,
)
from identity.settings import IdentitySettings

logger = logging.getLogger(__name__)

SERVICE = "identity"
REFRESH_COOKIE = "am_refresh"
SERVICE_ACCOUNT_PREFIX = "service-account-"

outbox = Outbox(outbox_table)
public = APIRouter(prefix="/public")
router = APIRouter()


# --- зависимости ---------------------------------------------------------------


def _settings(resources: AppResources) -> IdentitySettings:
    return cast(IdentitySettings, resources.settings)


def get_auth(resources: AppResources) -> KeycloakAuth:
    settings = _settings(resources)
    return KeycloakAuth(
        resources.http,
        keycloak_url=settings.keycloak_url,
        realm=settings.keycloak_realm,
        client_id=settings.login_client_id,
    )


def get_admin(request: Request, resources: AppResources) -> KeycloakAdmin:
    """Один клиент Admin API на процесс: он кэширует служебный токен."""
    admin: KeycloakAdmin | None = getattr(request.app.state, "keycloak_admin", None)
    if admin is None:
        settings = _settings(resources)
        if settings.keycloak_client_secret is None:
            raise KeycloakUnavailableError("am-identity service account is not configured")
        admin = KeycloakAdmin(
            resources.http,
            keycloak_url=settings.keycloak_url,
            realm=settings.keycloak_realm,
            client_id=settings.keycloak_client_id,
            client_secret=settings.keycloak_client_secret.get_secret_value(),
        )
        request.app.state.keycloak_admin = admin
    return admin


Session = Annotated[AsyncSession, Depends(get_session)]
Auth = Annotated[KeycloakAuth, Depends(get_auth)]
Admin = Annotated[KeycloakAdmin, Depends(get_admin)]
Settings = Annotated[IdentitySettings, Depends(_settings)]
AdminPrincipal = Annotated[Principal, Depends(require_any_role(Role.ADMIN))]
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE)]


# --- модели --------------------------------------------------------------------

Password = Annotated[str, Field(min_length=1, max_length=1024)]


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=255)
    password: Password
    remember: bool = False


class FirstLoginIn(LoginIn):
    new_password: Password


class AcceptIn(BaseModel):
    password: Password
    remember: bool = False


class PasswordIn(BaseModel):
    current_password: Password
    new_password: Password


class SessionUser(BaseModel):
    id: str
    username: str
    display_name: str
    roles: list[str]


class SessionOut(BaseModel):
    access_token: str
    expires_in: int
    user: SessionUser


class InviteInfo(BaseModel):
    username: str
    full_name: str
    expires_at: datetime


class InviteIn(BaseModel):
    ttl_days: InviteTtl = 7


class InviteLink(BaseModel):
    """Секрет ссылки: показываем один раз, в базе — только его хэш."""

    token: str
    expires_at: datetime


class NewUserIn(BaseModel):
    username: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: str | None = Field(default=None, max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    roles: list[HumanRole] = Field(default_factory=list)
    ttl_days: InviteTtl = 7

    @field_validator("username", mode="before")
    @classmethod
    def _lower(cls, value: Any) -> Any:
        # Keycloak хранит логины строчными.
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("first_name", "last_name", "email", mode="before")
    @classmethod
    def _strip(cls, value: Any) -> Any:
        return (value.strip() or None) if isinstance(value, str) else value


class RolesIn(BaseModel):
    roles: list[HumanRole]


class EnabledIn(BaseModel):
    enabled: bool


class UserOut(BaseModel):
    id: str
    username: str
    first_name: str
    last_name: str
    full_name: str
    email: str | None
    enabled: bool
    roles: list[str]
    status: UserStatus
    # Срок открытой ссылки: она не использована и не заменена новой.
    invite_expires_at: datetime | None
    last_login_at: datetime | None


class CreatedOut(BaseModel):
    user: UserOut
    invite: InviteLink


# --- вход ----------------------------------------------------------------------


@public.post("/auth/login")
async def login(
    body: LoginIn,
    *,
    request: Request,
    response: Response,
    auth: Auth,
    session: Session,
    settings: Settings,
) -> SessionOut:
    tokens = await auth.login(body.username, body.password, remember=body.remember)
    return await _signed_in(request, response, session, settings, tokens)


@public.post("/auth/refresh", response_model=SessionOut)
async def refresh(
    request: Request,
    response: Response,
    auth: Auth,
    settings: Settings,
    refresh_token: RefreshCookie = None,
) -> Response | SessionOut:
    """Новый access token по cookie — при открытии страницы и раз в несколько минут."""
    if refresh_token is None:
        raise IdentityError(401, "no_session", "Войдите в систему.")
    try:
        tokens = await auth.refresh(refresh_token)
    except IdentityError as exc:
        failed = _error_response(exc)
        _delete_cookie(request, failed)
        return failed
    _set_cookie(request, response, settings, tokens)
    return _session_out(tokens)


@public.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, auth: Auth, refresh_token: RefreshCookie = None) -> Response:
    if refresh_token is not None:
        await auth.logout(refresh_token)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _delete_cookie(request, response)
    return response


@public.post("/auth/first-login")
async def first_login(
    body: FirstLoginIn,
    *,
    request: Request,
    response: Response,
    auth: Auth,
    admin: Admin,
    session: Session,
    settings: Settings,
) -> SessionOut:
    """Первый вход с временным паролем: сразу задаём свой и входим."""
    check_password(body.new_password, body.username)
    if body.new_password == body.password:
        raise IdentityError(422, "same_password", "Новый пароль совпадает с временным.")
    try:
        checked = await auth.login(body.username, body.password, remember=False)
    except IdentityError as exc:
        # Keycloak отвечает «задайте пароль», только если временный пароль верный.
        if exc.code != "password_change_required":
            raise
    else:
        await auth.logout(checked.refresh_token)
        raise IdentityError(409, "password_already_set", "Пароль уже задан — войдите с ним.")
    user = await admin.find(body.username)
    user_id, username = str(user["id"]), str(user["username"])
    await _set_password(admin, user, body.new_password)
    async with session.begin():
        await _event(
            session,
            "user.password_set",
            Actor(subject=user_id, username=username),
            user_id,
            {"user": _ref(user_id, username), "after": {"via": "temporary"}},
        )
    tokens = await auth.login(username, body.new_password, remember=body.remember)
    return await _signed_in(request, response, session, settings, tokens)


# --- приглашение ---------------------------------------------------------------


@public.get("/invites/{token}")
async def invite_info(token: str, admin: Admin, session: Session) -> InviteInfo:
    async with session.begin():
        row = _open_invite(await repository.find_invite(session, token))
    user = await admin.user(row.user_id)
    return InviteInfo(username=row.username, full_name=_full_name(user), expires_at=row.expires_at)


@public.post("/invites/{token}/accept")
async def accept_invite(
    token: str,
    body: AcceptIn,
    *,
    request: Request,
    response: Response,
    auth: Auth,
    admin: Admin,
    session: Session,
    settings: Settings,
) -> SessionOut:
    """Пароль по ссылке — и сразу вход. Строка ссылки заблокирована: дважды не задать."""
    async with session.begin():
        row = _open_invite(await repository.find_invite(session, token, for_update=True))
        check_password(body.password, row.username)
        user = await admin.user(row.user_id)
        if not user.get("enabled", True):
            raise IdentityError(
                403,
                "account_disabled",
                "Учётная запись заблокирована. Обратитесь к администратору.",
            )
        await _set_password(admin, user, body.password)
        await repository.mark_used(session, row.id, _now())
        await _event(
            session,
            "user.password_set",
            Actor(subject=row.user_id, username=row.username),
            row.user_id,
            {"user": _ref(row.user_id, row.username), "after": {"via": "invite"}},
        )
    tokens = await auth.login(row.username, body.password, remember=body.remember)
    return await _signed_in(request, response, session, settings, tokens)


# --- свой пароль ---------------------------------------------------------------


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    body: PasswordIn, principal: CurrentPrincipal, auth: Auth, admin: Admin, session: Session
) -> None:
    check_password(body.new_password, principal.username)
    if body.new_password == body.current_password:
        raise IdentityError(422, "same_password", "Новый пароль совпадает с текущим.")
    try:
        checked = await auth.login(principal.username, body.current_password, remember=False)
    except IdentityError as exc:
        if exc.code == "invalid_credentials":
            raise IdentityError(422, "wrong_password", "Текущий пароль указан неверно.") from exc
        raise
    # Проверочный вход — не сессия: сразу закрываем.
    await auth.logout(checked.refresh_token)
    await admin.set_password(principal.subject, body.new_password)
    async with session.begin():
        await _event(
            session,
            "user.password_changed",
            Actor.from_principal(principal),
            principal.subject,
            {"user": _ref(principal.subject, principal.username)},
        )


# --- пользователи (администратор) ----------------------------------------------


@router.get("/users")
async def list_users(_: AdminPrincipal, admin: Admin, session: Session) -> list[UserOut]:
    users = [u for u in await admin.users() if not _is_service_account(u)]
    # Роли — у каждого пользователя: прав читать участников роли у сервиса нет.
    roles = await asyncio.gather(*(admin.realm_roles(str(u["id"])) for u in users))
    async with session.begin():
        invites = await repository.latest_invites(session)
        logins = await repository.last_logins(session)
    now = _now()
    return [
        _user_out(user, user_roles, invites.get(str(user["id"])), logins, now)
        for user, user_roles in zip(users, roles, strict=True)
    ]


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    body: NewUserIn, principal: AdminPrincipal, admin: Admin, session: Session
) -> CreatedOut:
    """Новый пользователь без пароля и ссылка, по которой он его задаст."""
    user_id = await admin.create(
        {
            "username": body.username,
            "firstName": body.first_name,
            "lastName": body.last_name,
            "email": body.email,
            "enabled": True,
        }
    )
    roles = [role for role in HUMAN_ROLES if role in body.roles]
    if roles:
        await admin.add_roles(user_id, roles)
    actor = Actor.from_principal(principal)
    async with session.begin():
        issued = await repository.issue_invite(
            session,
            user_id=user_id,
            username=body.username,
            ttl_days=body.ttl_days,
            created_by=principal.username,
            now=_now(),
        )
        await _event(
            session,
            "user.created",
            actor,
            user_id,
            {
                "user": _ref(user_id, body.username),
                "after": {
                    "username": body.username,
                    "full_name": f"{body.first_name} {body.last_name}",
                    "email": body.email,
                    "roles": roles,
                },
            },
        )
        await _event(
            session, "user.invited", actor, user_id, _invited(user_id, body.username, issued)
        )
    return CreatedOut(
        user=await _load_user(admin, session, user_id),
        invite=InviteLink(token=issued.token, expires_at=issued.expires_at),
    )


@router.put("/users/{user_id}/roles")
async def set_roles(
    user_id: str, body: RolesIn, principal: AdminPrincipal, admin: Admin, session: Session
) -> UserOut:
    user = await _managed_user(admin, user_id)
    wanted = set(body.roles)
    if user_id == principal.subject and "admin" not in wanted:
        raise IdentityError(409, "self_admin", "Нельзя снять роль администратора с самого себя.")
    have = await admin.realm_roles(user_id)
    username = str(user["username"])
    added = [role for role in HUMAN_ROLES if role in wanted and role not in have]
    removed = [role for role in HUMAN_ROLES if role in have and role not in wanted]
    if added:
        await admin.add_roles(user_id, added)
    if removed:
        await admin.remove_roles(user_id, removed)
    if added or removed:
        async with session.begin():
            await _event(
                session,
                "user.roles_changed",
                Actor.from_principal(principal),
                user_id,
                {
                    "user": _ref(user_id, username),
                    "before": {"roles": [role for role in HUMAN_ROLES if role in have]},
                    "after": {"roles": [role for role in HUMAN_ROLES if role in wanted]},
                },
            )
    return await _load_user(admin, session, user_id)


@router.put("/users/{user_id}/enabled")
async def set_enabled(
    user_id: str, body: EnabledIn, principal: AdminPrincipal, admin: Admin, session: Session
) -> UserOut:
    """Блокировка сразу завершает все сессии пользователя, «запомненные» тоже."""
    user = await _managed_user(admin, user_id)
    if user_id == principal.subject and not body.enabled:
        raise IdentityError(409, "self_block", "Нельзя заблокировать самого себя.")
    was_enabled = bool(user.get("enabled", True))
    if was_enabled != body.enabled:
        await admin.update(user_id, {**user, "enabled": body.enabled})
        if not body.enabled:
            await admin.logout(user_id)
        async with session.begin():
            await _event(
                session,
                "user.unblocked" if body.enabled else "user.blocked",
                Actor.from_principal(principal),
                user_id,
                {
                    "user": _ref(user_id, str(user["username"])),
                    "before": {"enabled": was_enabled},
                    "after": {"enabled": body.enabled},
                },
            )
    return await _load_user(admin, session, user_id)


@router.post("/users/{user_id}/invite", status_code=status.HTTP_201_CREATED)
async def invite(
    user_id: str, body: InviteIn, principal: AdminPrincipal, admin: Admin, session: Session
) -> InviteLink:
    """Новая ссылка: первый вход или сброс забытого пароля. Прежние ссылки перестают работать."""
    user = await _managed_user(admin, user_id)
    if not user.get("enabled", True):
        raise IdentityError(
            409, "user_blocked", "Пользователь заблокирован — сначала разблокируйте его."
        )
    username = str(user["username"])
    async with session.begin():
        issued = await repository.issue_invite(
            session,
            user_id=user_id,
            username=username,
            ttl_days=body.ttl_days,
            created_by=principal.username,
            now=_now(),
        )
        await _event(
            session,
            "user.invited",
            Actor.from_principal(principal),
            user_id,
            _invited(user_id, username, issued),
        )
    return InviteLink(token=issued.token, expires_at=issued.expires_at)


# --- ошибки --------------------------------------------------------------------


def install_error_handlers(app: FastAPI) -> None:
    async def identity_error(_request: Request, exc: Exception) -> Response:
        return _error_response(cast(IdentityError, exc))

    async def keycloak_unavailable(_request: Request, exc: Exception) -> Response:
        logger.error("keycloak unavailable: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": {
                    "code": "keycloak_unavailable",
                    "message": "Сервис входа (Keycloak) недоступен. Повторите позже.",
                }
            },
        )

    app.add_exception_handler(IdentityError, identity_error)
    app.add_exception_handler(KeycloakUnavailableError, keycloak_unavailable)


# --- помощники -----------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _unverified(token: str) -> dict[str, Any]:
    """Claims токена, который мы только что получили от Keycloak сами, — без проверки подписи."""
    try:
        claims: dict[str, Any] = jwt.decode(token, options={"verify_signature": False})
        return claims
    except jwt.PyJWTError:
        return {}


def _session_out(tokens: Tokens) -> SessionOut:
    claims = _unverified(tokens.access_token)
    username = str(claims.get("preferred_username") or claims["sub"])
    full_name = " ".join(
        str(part) for part in (claims.get("given_name"), claims.get("family_name")) if part
    )
    raw_roles = (claims.get("realm_access") or {}).get("roles") or []
    return SessionOut(
        access_token=tokens.access_token,
        expires_in=tokens.expires_in,
        user=SessionUser(
            id=str(claims["sub"]),
            username=username,
            display_name=full_name or username,
            roles=[role for role in HUMAN_ROLES if role in raw_roles],
        ),
    )


async def _signed_in(
    request: Request,
    response: Response,
    session: AsyncSession,
    settings: IdentitySettings,
    tokens: Tokens,
) -> SessionOut:
    """Вход состоялся: cookie с refresh token, время входа, событие в журнал."""
    out = _session_out(tokens)
    remember = _set_cookie(request, response, settings, tokens)
    async with session.begin():
        await repository.record_login(session, out.user.id, _now())
        await _event(
            session,
            "user.logged_in",
            Actor(subject=out.user.id, username=out.user.username),
            out.user.id,
            {"user": _ref(out.user.id, out.user.username), "after": {"remember": remember}},
        )
    return out


def _cookie_path(request: Request) -> str:
    # Браузер видит сервис под /api/identity: префикс — root_path.
    return f"{request.scope.get('root_path', '')}/public/auth"


def _set_cookie(
    request: Request, response: Response, settings: IdentitySettings, tokens: Tokens
) -> bool:
    """Cookie с refresh token; возвращает, «запомнен» ли вход.

    Офлайн-токен («Запомнить меня») — cookie со сроком: переживает закрытие
    браузера. Обычный — cookie сессии браузера.
    """
    remember = _unverified(tokens.refresh_token).get("typ") == "Offline"
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        max_age=(tokens.refresh_expires_in or settings.remember_max_age) if remember else None,
        path=_cookie_path(request),
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )
    return remember


def _delete_cookie(request: Request, response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path=_cookie_path(request),
        httponly=True,
        samesite="strict",
        secure=request.url.scheme == "https",
    )


def _error_response(exc: IdentityError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status, content={"detail": {"code": exc.code, "message": exc.message}}
    )


def _open_invite(row: Row[Any] | None) -> Row[Any]:
    if row is None:
        raise IdentityError(
            404,
            "invite_not_found",
            "Ссылка не найдена. Проверьте, что скопировали её целиком, или попросите новую.",
        )
    check_invite(
        expires_at=row.expires_at, used_at=row.used_at, revoked_at=row.revoked_at, now=_now()
    )
    return row


async def _set_password(admin: KeycloakAdmin, user: dict[str, Any], password: str) -> None:
    """Постоянный пароль и никаких обязательных действий: страниц Keycloak пользователь не видит."""
    user_id = str(user["id"])
    await admin.set_password(user_id, password)
    if user.get("requiredActions"):
        await admin.update(user_id, {**user, "requiredActions": []})


async def _event(
    session: AsyncSession, event_type: str, actor: Actor, user_id: str, data: dict[str, Any]
) -> None:
    await outbox.add(
        session,
        Event(
            service=SERVICE,
            type=event_type,
            actor=actor,
            entity_type="user",
            entity_id=user_id,
            data=data,
        ),
    )


def _ref(user_id: str, username: str) -> dict[str, str]:
    """Пользователь в событии — так его показывает журнал."""
    return {"subject": user_id, "username": username}


def _invited(user_id: str, username: str, issued: repository.Issued) -> dict[str, Any]:
    return {
        "user": _ref(user_id, username),
        "after": {"expires_at": issued.expires_at.isoformat()},
    }


def _is_service_account(user: dict[str, Any]) -> bool:
    return str(user["username"]).startswith(SERVICE_ACCOUNT_PREFIX)


async def _managed_user(admin: KeycloakAdmin, user_id: str) -> dict[str, Any]:
    user = await admin.user(user_id)
    if _is_service_account(user):
        raise IdentityError(404, "user_not_found", "Служебные учётные записи здесь не меняются.")
    return user


def _full_name(user: dict[str, Any]) -> str:
    return " ".join(str(user[key]) for key in ("firstName", "lastName") if user.get(key))


def _user_out(
    user: dict[str, Any],
    roles: set[str],
    invite: Row[Any] | None,
    logins: dict[str, datetime],
    now: datetime,
) -> UserOut:
    user_id = str(user["id"])
    expires_at = None
    if invite is not None and invite.used_at is None and invite.revoked_at is None:
        expires_at = invite.expires_at
    enabled = bool(user.get("enabled", True))
    return UserOut(
        id=user_id,
        username=str(user["username"]),
        first_name=str(user.get("firstName") or ""),
        last_name=str(user.get("lastName") or ""),
        full_name=_full_name(user),
        email=user.get("email") or None,
        enabled=enabled,
        roles=[role for role in HUMAN_ROLES if role in roles],
        status=user_status(
            enabled=enabled,
            open_invite_expires_at=expires_at,
            has_logged_in=user_id in logins,
            now=now,
        ),
        invite_expires_at=expires_at,
        last_login_at=logins.get(user_id),
    )


async def _load_user(admin: KeycloakAdmin, session: AsyncSession, user_id: str) -> UserOut:
    user = await admin.user(user_id)
    roles = await admin.realm_roles(user_id)
    async with session.begin():
        invites = await repository.latest_invites(session)
        logins = await repository.last_logins(session)
    return _user_out(user, roles, invites.get(user_id), logins, _now())
