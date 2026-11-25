"""Обновление уже работающей ВМ: недостающие секреты в .env и настройки Keycloak.

Keycloak импортирует realm только в пустую базу, а .env собирают один раз
(scripts/vm_config.py). Когда в realm появляются новые клиенты и настройки —
например, свой вход (решение 170), — машина, поднятая раньше, их не получит.
Скрипт догоняет её до текущего realm для ВМ:

1. дописывает в .env секреты служебных учётных записей, которых там нет, —
   случайные; существующие строки не трогает, старые пароли подходят к базе;
2. приводит Keycloak к realm для ВМ: роли, клиенты (секрет нового клиента — из
   .env), настройки realm и входа, права служебных учётных записей;
3. выдаёт роль offline_access («Запомнить меня») людям, у которых её нет.

Ничего не удаляет, людей не заводит, их пароли не трогает. Повторный запуск
ничего не меняет. Только стандартная библиотека: хватит системного python3.

    git pull
    python3 scripts/vm_upgrade.py            # что сделает
    python3 scripts/vm_upgrade.py --apply    # сделать
    docker compose up -d --build
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

# Соседние скрипты: python3 scripts/vm_upgrade.py кладёт их папку в sys.path.
from keycloak_sync import REALM_SETTINGS, Keycloak, sync
from vm_config import CLIENT_SECRETS, DEV_REALM, ROOT, ConfigError, token, vm_realm

# На ВМ к настройкам входа добавляется политика паролей: в realm разработки её нет.
VM_SETTINGS = (*REALM_SETTINGS, "passwordPolicy")
# Роль «Запомнить меня»: люди из файла realm не получают роли по умолчанию.
REMEMBER_ROLE = "offline_access"
SERVICE_ACCOUNT_PREFIX = "service-account-"


def read_env(path: Path) -> dict[str, str]:
    """Строки KEY=VALUE файла .env; комментарии и пустые строки пропускаются."""
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key, value = stripped.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def check_machine(env: dict[str, str]) -> None:
    """Скрипт — для .env машины из vm_config.py, а не для разработки."""
    if env.get("KEYCLOAK_MODE") != "start" or not env.get("PUBLIC_URL", "").startswith("https://"):
        raise ConfigError(
            "Это не .env виртуальной машины: нет KEYCLOAK_MODE=start и адреса https://. "
            "Для разработки — make keycloak-sync."
        )
    for name in ("KEYCLOAK_ADMIN_USER", "KEYCLOAK_ADMIN_PASSWORD"):
        if not env.get(name):
            raise ConfigError(f"В .env нет {name}: без него не войти в консоль Keycloak.")


def missing_secrets(env: dict[str, str]) -> list[str]:
    return [name for name in CLIENT_SECRETS.values() if not env.get(name)]


def append_secrets(path: Path, names: list[str], today: date) -> dict[str, str]:
    """Дописывает случайные секреты в конец .env; возвращает их."""
    values = {name: token() for name in names}
    text = path.read_text(encoding="utf-8")
    lines = [
        "" if text.endswith("\n") else "\n",
        f"# Дописано scripts/vm_upgrade.py {today.isoformat()}: секреты новых служебных учёток.",
        *(f"{name}={value}" for name, value in values.items()),
        "",
    ]
    with path.open("a", encoding="utf-8", newline="\n") as file:
        file.write("\n".join(lines))
    return values


def machine_realm(dev: dict[str, Any], env: dict[str, str]) -> dict[str, Any]:
    """Realm для ВМ, как его собрал бы vm_config.py сейчас, — без людей.

    Секреты — из .env. Секрет разработки (он лежит в открытом репозитории) на
    машине недопустим: с ним кто угодно получил бы права служебной учётной записи.
    """
    dev_secrets = {client["clientId"]: client.get("secret") for client in dev["clients"]}
    values = {name: env[name] for name in CLIENT_SECRETS.values()}
    for client_id, name in CLIENT_SECRETS.items():
        if values[name] == dev_secrets.get(client_id):
            raise ConfigError(f"{name} в .env — секрет разработки. Задайте случайный.")
    realm = vm_realm(dev, env["PUBLIC_URL"], values, admin_password="", email="")
    # Людей на машине заводят в приложении — скрипт их не создаёт.
    realm["users"] = [user for user in realm["users"] if user.get("serviceAccountClientId")]
    return realm


def grant_remember(kc: Keycloak, admin: str, *, apply: bool) -> list[str]:
    """Роль offline_access тем людям, у кого её нет ни прямо, ни через роли по умолчанию."""
    plan: list[str] = []
    role: Any = None
    for user in kc.call("GET", f"{admin}/users?max=1000&briefRepresentation=true"):
        if str(user["username"]).startswith(SERVICE_ACCOUNT_PREFIX):
            continue
        effective = kc.call("GET", f"{admin}/users/{user['id']}/role-mappings/realm/composite")
        if any(r["name"] == REMEMBER_ROLE for r in effective):
            continue
        plan.append(f"роль {REMEMBER_ROLE} у {user['username']}")
        if apply:
            role = role or kc.call("GET", f"{admin}/roles/{REMEMBER_ROLE}")
            kc.call("POST", f"{admin}/users/{user['id']}/role-mappings/realm", [role])
    return plan


def upgrade(
    env_path: Path,
    connect: Callable[[str, str, str], Keycloak],
    *,
    url: str | None,
    apply: bool,
    today: date,
    root: Path = ROOT,
) -> list[str]:
    """Что сделать с .env и Keycloak; с ``apply`` — сделать."""
    if not env_path.exists():
        raise ConfigError(f"Нет {env_path}: скрипт — для машины, настроенной scripts/vm_config.py.")
    env = read_env(env_path)
    check_machine(env)
    plan: list[str] = []
    missing = missing_secrets(env)
    plan.extend(f".env: {name} — новый случайный секрет" for name in missing)
    if missing:
        # Без --apply .env не меняем: план строим с временными значениями.
        env.update(
            append_secrets(env_path, missing, today)
            if apply
            else {name: token() for name in missing}
        )
    dev = json.loads((root / DEV_REALM).read_text(encoding="utf-8"))
    realm = machine_realm(dev, env)
    port = env.get("GATEWAY_PORT") or "8080"
    kc = connect(
        url or f"http://127.0.0.1:{port}/auth",
        env["KEYCLOAK_ADMIN_USER"],
        env["KEYCLOAK_ADMIN_PASSWORD"],
    )
    plan.extend(sync(kc, realm, apply=apply, settings=VM_SETTINGS))
    plan.extend(grant_remember(kc, f"/admin/realms/{realm['realm']}", apply=apply))
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Обновление уже работающей ВМ: .env и Keycloak.")
    parser.add_argument("--apply", action="store_true", help="сделать, а не только показать")
    parser.add_argument("--env", type=Path, default=ROOT / ".env", help="файл .env машины")
    parser.add_argument("--url", help="адрес Keycloak; по умолчанию — через шлюз на 127.0.0.1")
    args = parser.parse_args(argv)
    try:
        plan = upgrade(args.env, Keycloak, url=args.url, apply=args.apply, today=date.today())
    except ConfigError as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    except urllib.error.HTTPError as exc:
        print(f"Keycloak ответил {exc.code} на {exc.url}: {exc.read().decode(errors='replace')}")
        return 1
    except urllib.error.URLError as exc:
        print(f"Keycloak недоступен: {exc.reason}. Окружение запущено (docker compose up -d)?")
        return 1
    if not plan:
        print("Машина совпадает с realm — менять нечего.")
        return 0
    print("Сделано:" if args.apply else "Сделает (запустите с --apply):")
    for item in plan:
        print(" -", item)
    if args.apply:
        print("Теперь: docker compose up -d --build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
