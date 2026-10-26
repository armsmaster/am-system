"""HTTP API сервиса портфелей. Правила — в docs/design/01-portfolio-audit.md."""

from __future__ import annotations

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StringConstraints

from am_common.app import AppResources, DbSession
from am_common.auth import CurrentPrincipal, Principal, Role, require_any_role
from am_common.events import Actor
from portfolio.commands import Commands, NewAccount, NewPortfolio
from portfolio.directory import DirectoryUnavailableError, KeycloakDirectory, UserDirectory
from portfolio.domain import Account, CashKind, Kind, NotFoundError, RuleViolationError
from portfolio.queries import PortfolioView, portfolio_views, visibility, visible_accounts
from portfolio.repository import Repository
from portfolio.settings import PortfolioSettings

Code = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=32, pattern=r"^\S+$")
]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

AdminPrincipal = Annotated[Principal, Depends(require_any_role(Role.ADMIN))]

router = APIRouter()


# --- справочник пользователей ----------------------------------------------------


def get_directory(request: Request, resources: AppResources) -> UserDirectory:
    """Один клиент Keycloak на процесс: он кэширует служебный токен."""
    directory: UserDirectory | None = getattr(request.app.state, "directory", None)
    if directory is None:
        settings = cast(PortfolioSettings, resources.settings)
        if settings.keycloak_client_secret is None:
            raise DirectoryUnavailableError("keycloak service account is not configured")
        directory = KeycloakDirectory(
            resources.http,
            base_url=settings.keycloak_url,
            realm=settings.keycloak_realm,
            client_id=settings.keycloak_client_id,
            client_secret=settings.keycloak_client_secret.get_secret_value(),
        )
        request.app.state.directory = directory
    return directory


Directory = Annotated[UserDirectory, Depends(get_directory)]


# --- схемы ответа и запросов -------------------------------------------------------


class MeOut(BaseModel):
    subject: str
    username: str
    roles: list[Role]
    sees_everything: bool
    portfolio_ids: list[UUID]


class PortfolioOut(BaseModel):
    id: UUID
    code: str
    name: str
    kind: Kind
    is_fund: bool
    parent_ids: list[UUID]
    fund_id: UUID | None
    account_id: UUID | None
    treasury_id: UUID | None

    @classmethod
    def of(cls, view: PortfolioView) -> PortfolioOut:
        item = view.portfolio
        return cls(
            id=item.id,
            code=item.code,
            name=item.name,
            kind=item.kind,
            is_fund=item.is_fund,
            parent_ids=view.parent_ids,
            fund_id=view.fund_id,
            account_id=item.account_id,
            treasury_id=item.treasury_id,
        )


class NewPortfolioIn(BaseModel):
    code: Code
    name: Name
    kind: Kind
    is_fund: bool = False
    parent_ids: list[UUID] = []
    account_id: UUID | None = None


class PortfolioChangesIn(BaseModel):
    code: Code | None = None
    name: Name | None = None


class AccountRefIn(BaseModel):
    account_id: UUID


class TreasuryIn(BaseModel):
    portfolio_id: UUID | None


class AccountOut(BaseModel):
    id: UUID
    fund_id: UUID
    code: str
    name: str
    cash_kind: CashKind
    institution: str
    cash_account: str
    depo_account: str | None
    stock_market: bool
    derivatives_market: bool

    @classmethod
    def of(cls, item: Account) -> AccountOut:
        return cls.model_validate(item, from_attributes=True)


class NewAccountIn(BaseModel):
    fund_id: UUID
    code: Code
    name: Name
    cash_kind: CashKind
    institution: Text
    cash_account: Text
    depo_account: Text | None = None
    stock_market: bool = True
    derivatives_market: bool = False


class AccountChangesIn(BaseModel):
    code: Code | None = None
    name: Name | None = None
    cash_kind: CashKind | None = None
    institution: Text | None = None
    cash_account: Text | None = None
    depo_account: Text | None = None
    stock_market: bool | None = None
    derivatives_market: bool | None = None


class UserOut(BaseModel):
    subject: str
    username: str
    full_name: str
    enabled: bool
    roles: list[Role]
    grant_ids: list[UUID]


# --- чтение ---------------------------------------------------------------------


@router.get("/me", tags=["me"])
async def me(principal: CurrentPrincipal, session: DbSession) -> MeOut:
    """Кто я и какие портфели мне доступны."""
    scope = await visibility(session, principal)
    return MeOut(
        subject=principal.subject,
        username=principal.username,
        roles=sorted(principal.roles),
        sees_everything=scope.sees_everything,
        portfolio_ids=sorted(scope.visible),
    )


@router.get("/portfolios", tags=["portfolios"])
async def list_portfolios(principal: CurrentPrincipal, session: DbSession) -> list[PortfolioOut]:
    """Видимые пользователю портфели со связями, фондом и счётом."""
    scope = await visibility(session, principal)
    return [PortfolioOut.of(view) for view in portfolio_views(scope)]


@router.get("/accounts", tags=["accounts"])
async def list_accounts(principal: CurrentPrincipal, session: DbSession) -> list[AccountOut]:
    scope = await visibility(session, principal)
    return [AccountOut.of(item) for item in visible_accounts(scope)]


@router.get("/users", tags=["access"])
async def list_users(
    _admin: AdminPrincipal, session: DbSession, directory: Directory
) -> list[UserOut]:
    """Пользователи Keycloak с ролями и выданными доступами."""
    users = await directory.users()
    grants = await Repository(session).grants()
    return [
        UserOut(
            subject=user.subject,
            username=user.username,
            full_name=user.full_name,
            enabled=user.enabled,
            roles=sorted(user.roles),
            grant_ids=sorted(grants.get(user.subject, ())),
        )
        for user in users
    ]


# --- изменения (только администратор) ---------------------------------------------


@router.post("/portfolios", status_code=status.HTTP_201_CREATED, tags=["portfolios"])
async def create_portfolio(
    body: NewPortfolioIn, admin: AdminPrincipal, session: DbSession
) -> PortfolioOut:
    created = await _commands(session, admin).create_portfolio(
        NewPortfolio(
            code=body.code,
            name=body.name,
            kind=body.kind,
            is_fund=body.is_fund,
            parent_ids=tuple(body.parent_ids),
            account_id=body.account_id,
        )
    )
    return await _portfolio_out(session, admin, created.id)


@router.patch("/portfolios/{portfolio_id}", tags=["portfolios"])
async def update_portfolio(
    portfolio_id: UUID, body: PortfolioChangesIn, admin: AdminPrincipal, session: DbSession
) -> PortfolioOut:
    await _commands(session, admin).update_portfolio(portfolio_id, code=body.code, name=body.name)
    return await _portfolio_out(session, admin, portfolio_id)


@router.put(
    "/portfolios/{parent_id}/children/{child_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["portfolios"],
)
async def link(parent_id: UUID, child_id: UUID, admin: AdminPrincipal, session: DbSession) -> None:
    """Включить портфель в compound."""
    await _commands(session, admin).link(parent_id, child_id)


@router.delete(
    "/portfolios/{parent_id}/children/{child_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["portfolios"],
)
async def unlink(
    parent_id: UUID, child_id: UUID, admin: AdminPrincipal, session: DbSession
) -> None:
    """Исключить портфель из compound."""
    await _commands(session, admin).unlink(parent_id, child_id)


@router.put("/portfolios/{portfolio_id}/account", tags=["portfolios"])
async def set_account(
    portfolio_id: UUID, body: AccountRefIn, admin: AdminPrincipal, session: DbSession
) -> PortfolioOut:
    await _commands(session, admin).set_account(portfolio_id, body.account_id)
    return await _portfolio_out(session, admin, portfolio_id)


@router.put("/funds/{fund_id}/treasury", tags=["portfolios"])
async def set_treasury(
    fund_id: UUID, body: TreasuryIn, admin: AdminPrincipal, session: DbSession
) -> PortfolioOut:
    await _commands(session, admin).set_treasury(fund_id, body.portfolio_id)
    return await _portfolio_out(session, admin, fund_id)


@router.post("/accounts", status_code=status.HTTP_201_CREATED, tags=["accounts"])
async def create_account(
    body: NewAccountIn, admin: AdminPrincipal, session: DbSession
) -> AccountOut:
    created = await _commands(session, admin).create_account(NewAccount(**body.model_dump()))
    return AccountOut.of(created)


@router.patch("/accounts/{account_id}", tags=["accounts"])
async def update_account(
    account_id: UUID, body: AccountChangesIn, admin: AdminPrincipal, session: DbSession
) -> AccountOut:
    changes = body.model_dump(exclude_unset=True)
    updated = await _commands(session, admin).update_account(account_id, **changes)
    return AccountOut.of(updated)


@router.put(
    "/users/{subject}/grants/{portfolio_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["access"],
)
async def grant(
    subject: str,
    portfolio_id: UUID,
    admin: AdminPrincipal,
    session: DbSession,
    directory: Directory,
) -> None:
    """Выдать пользователю доступ к compound (и всем его потомкам)."""
    await _commands(session, admin).grant(subject, portfolio_id, directory)


@router.delete(
    "/users/{subject}/grants/{portfolio_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["access"],
)
async def revoke(
    subject: str, portfolio_id: UUID, admin: AdminPrincipal, session: DbSession
) -> None:
    await _commands(session, admin).revoke(subject, portfolio_id)


# --- ошибки предметной области → ответы HTTP -------------------------------------


def install_error_handlers(app: FastAPI) -> None:
    async def rule_violation(_request: Request, exc: Exception) -> Response:
        violation = cast(RuleViolationError, exc)
        return JSONResponse(
            status_code=422,
            content={"detail": {"code": violation.code, "message": violation.message}},
        )

    async def not_found(_request: Request, exc: Exception) -> Response:
        missing = cast(NotFoundError, exc)
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": {"code": "not_found", "message": f"Не найдено: {missing.what}"}},
        )

    async def directory_unavailable(_request: Request, _exc: Exception) -> Response:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": {
                    "code": "directory_unavailable",
                    "message": "Справочник пользователей (Keycloak) недоступен.",
                }
            },
        )

    app.add_exception_handler(RuleViolationError, rule_violation)
    app.add_exception_handler(NotFoundError, not_found)
    app.add_exception_handler(DirectoryUnavailableError, directory_unavailable)


# --- вспомогательное ------------------------------------------------------------


def _commands(session: DbSession, principal: Principal) -> Commands:
    return Commands(session, Actor.from_principal(principal))


async def _portfolio_out(
    session: DbSession, principal: Principal, portfolio_id: UUID
) -> PortfolioOut:
    scope = await visibility(session, principal)
    (view,) = (v for v in portfolio_views(scope) if v.portfolio.id == portfolio_id)
    return PortfolioOut.of(view)
