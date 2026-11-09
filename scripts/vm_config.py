"""Настройки для виртуальной машины: .env и realm Keycloak без значений разработки.

Режим разработки держит в репозитории всё для запуска одной командой: адрес
localhost, пароли вида admin-dev, секреты служебных учётных записей. На машине
в интернете этого быть не должно. Скрипт готовит два файла, которых нет в git:

- .env — домен, HTTPS, рабочий режим Keycloak, случайные пароли и секреты;
- infra/keycloak/realm-vm/am-realm.json — realm разработки, где адреса веб-клиента
  заменены на домен, секреты — на случайные, а вместо пользователей разработки —
  один администратор с временным паролем: Keycloak попросит сменить его при входе.

Только стандартная библиотека: на Ubuntu 24.04 хватит системного python3.

    python3 scripts/vm_config.py --domain am.example.ru --email ops@example.ru
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEV_REALM = "infra/keycloak/realms/am-realm.json"
VM_REALM_DIR = "infra/keycloak/realm-vm"

# Секрет клиента → переменная .env. Секреты сервисов compose передаёт в контейнеры;
# секреты скриптов цен, сделок и выписок — для их авторов.
CLIENT_SECRETS = {
    "am-portfolio": "PORTFOLIO_KEYCLOAK_SECRET",
    "am-marketdata": "MARKETDATA_KEYCLOAK_SECRET",
    "am-booking": "BOOKING_KEYCLOAK_SECRET",
    "am-reconciliation": "RECONCILIATION_KEYCLOAK_SECRET",
    "am-limits": "LIMITS_KEYCLOAK_SECRET",
    "am-price-feed": "PRICE_FEED_SECRET",
    "am-trade-feed": "TRADE_FEED_SECRET",
    "am-statement-feed": "STATEMENT_FEED_SECRET",
}
PASSWORDS = (
    "POSTGRES_PASSWORD",
    "KEYCLOAK_DB_PASSWORD",
    "SERVICES_DB_PASSWORD",
    "KEYCLOAK_ADMIN_PASSWORD",
)
WEB_CLIENT = "am-web"


class ConfigError(Exception):
    """Настройки нельзя собрать: файл уже есть или realm не тот, что ждёт скрипт."""


def token() -> str:
    """Случайное значение без символов, которые ломают URL базы и SQL."""
    return secrets.token_urlsafe(24)


def env_text(domain: str, email: str, values: dict[str, str], today: date) -> str:
    lines = [
        f"# Виртуальная машина {domain}: собрано scripts/vm_config.py {today.isoformat()}.",
        "# Здесь пароли и секреты — не коммитьте и не пересылайте этот файл.",
        "",
        f"PUBLIC_URL=https://{domain}",
        f"DOMAIN={domain}",
        f"ACME_EMAIL={email}",
        "COMPOSE_PROFILES=tls",
        "GATEWAY_BIND=127.0.0.1",
        "KEYCLOAK_MODE=start",
        f"KEYCLOAK_REALM_DIR=./{VM_REALM_DIR}",
        "KEYCLOAK_ADMIN_USER=kcadmin",
        "",
        "# Пароли баз и консоли Keycloak.",
        *(f"{name}={values[name]}" for name in PASSWORDS),
        "",
        "# Секреты служебных учётных записей: сервисы и скрипты цен, сделок, выписок.",
        *(f"{name}={values[name]}" for name in CLIENT_SECRETS.values()),
        "",
    ]
    return "\n".join(lines)


def vm_realm(
    dev: dict[str, Any], url: str, values: dict[str, str], admin_password: str, email: str
) -> dict[str, Any]:
    """Realm для машины: адреса — домен, секреты — свои, из людей — один администратор."""
    realm: dict[str, Any] = json.loads(json.dumps(dev))
    clients = {client["clientId"]: client for client in realm["clients"]}
    missing = sorted(({WEB_CLIENT} | set(CLIENT_SECRETS)) - set(clients))
    if missing:
        raise ConfigError(f"В realm нет клиентов: {', '.join(missing)}.")
    web = clients[WEB_CLIENT]
    web["redirectUris"] = [f"{url}/*"]
    web["attributes"]["post.logout.redirect.uris"] = f"{url}/*"
    for client_id, name in CLIENT_SECRETS.items():
        clients[client_id]["secret"] = values[name]
    # Служебные учётные записи остаются: у них роли сервисов, паролей у них нет.
    services = [user for user in realm["users"] if user.get("serviceAccountClientId")]
    admin = {
        "username": "admin",
        "enabled": True,
        "firstName": "Администратор",
        "lastName": "AM",
        # С адресом Keycloak не просит заполнить профиль — только сменить пароль.
        "email": email,
        "emailVerified": True,
        "credentials": [{"type": "password", "value": admin_password, "temporary": True}],
        "realmRoles": ["admin"],
        # При импорте realm «temporary» смену пароля не требует — только это действие.
        "requiredActions": ["UPDATE_PASSWORD"],
    }
    realm["users"] = [admin, *services]
    # Без HTTPS вход только из внутренних сетей: снаружи — через Caddy.
    realm["sslRequired"] = "external"
    return realm


def build(domain: str, email: str, root: Path, *, force: bool, today: date) -> str:
    """Пишет .env и realm для машины; возвращает временный пароль администратора."""
    domain = domain.strip().lower().removeprefix("https://").rstrip("/")
    if not domain or "/" in domain:
        raise ConfigError("Домен — имя без схемы и пути, например am.example.ru.")
    env_path = root / ".env"
    realm_path = root / VM_REALM_DIR / "am-realm.json"
    existing = [p for p in (env_path, realm_path) if p.exists()]
    if existing and not force:
        names = ", ".join(str(p.relative_to(root)) for p in existing)
        raise ConfigError(
            f"Уже есть {names}. Новые пароли не подойдут к базе, которую создали со старыми: "
            "пересобирайте только до первого запуска, с --force."
        )
    values = {name: token() for name in (*PASSWORDS, *CLIENT_SECRETS.values())}
    admin_password = token()
    dev = json.loads((root / DEV_REALM).read_text(encoding="utf-8"))
    realm = vm_realm(dev, f"https://{domain}", values, admin_password, email)
    realm_path.parent.mkdir(parents=True, exist_ok=True)
    realm_path.write_text(json.dumps(realm, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    env_path.write_text(env_text(domain, email, values, today), encoding="utf-8")
    return admin_password


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Настройки для виртуальной машины.")
    parser.add_argument("--domain", required=True, help="домен приложения, например am.example.ru")
    parser.add_argument(
        "--email", required=True, help="адрес для Let's Encrypt: о сроке сертификата"
    )
    parser.add_argument("--force", action="store_true", help="перезаписать .env и realm")
    args = parser.parse_args(argv)
    try:
        password = build(args.domain, args.email, ROOT, force=args.force, today=date.today())
    except ConfigError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    print(f"Готово: .env и {VM_REALM_DIR}/am-realm.json.")
    print("Вход в приложение: admin, временный пароль ниже — Keycloak попросит сменить его.")
    print(f"  {password}")
    print("Консоль Keycloak: kcadmin, пароль — KEYCLOAK_ADMIN_PASSWORD в .env.")
    print("Запуск: docker compose up -d --build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
