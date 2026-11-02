"""Дописывает в работающий Keycloak то, что есть в realm, но чего нет в Keycloak.

Keycloak импортирует realm только в пустую базу. Когда в realm появляются новые
роли, клиенты или пользователи, их можно завести без `make reset`: скрипт сравнивает
infra/keycloak/realms/am-realm.json с работающим Keycloak и добавляет недостающее —
роли, клиенты, пользователей с паролями из realm, роли пользователей и служебных
учётных записей. Существующее не меняет и ничего не удаляет.

Только для разработки: пароли и секреты берёт из realm.

    uv run python scripts/keycloak_sync.py            # что добавит
    uv run python scripts/keycloak_sync.py --apply    # добавить
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

REALM_FILE = (
    Path(__file__).resolve().parent.parent / "infra" / "keycloak" / "realms" / "am-realm.json"
)


class Keycloak:
    def __init__(self, base: str, user: str, password: str) -> None:
        self._base = base.rstrip("/")
        token = self.call(
            "POST",
            "/realms/master/protocol/openid-connect/token",
            form={
                "grant_type": "password",
                "client_id": "admin-cli",
                "username": user,
                "password": password,
            },
        )
        self._token = token["access_token"]

    def call(
        self, method: str, path: str, body: Any = None, form: dict[str, str] | None = None
    ) -> Any:
        headers = {}
        data = None
        if getattr(self, "_token", None):
            headers["Authorization"] = f"Bearer {self._token}"
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        # Адрес Keycloak задаёт разработчик: --url или KEYCLOAK_URL.
        request = urllib.request.Request(  # noqa: S310
            self._base + path, data=data, headers=headers, method=method
        )
        with urllib.request.urlopen(request) as response:  # noqa: S310
            raw = response.read()
        return json.loads(raw) if raw else None


def sync(kc: Keycloak, realm: dict[str, Any], *, apply: bool) -> list[str]:
    admin = f"/admin/realms/{realm['realm']}"
    plan: list[str] = []

    have_roles = {r["name"] for r in kc.call("GET", f"{admin}/roles?max=1000")}
    for role in realm["roles"]["realm"]:
        if role["name"] not in have_roles:
            plan.append(f"роль {role['name']}")
            if apply:
                kc.call("POST", f"{admin}/roles", role)

    clients: dict[str, str] = {}
    for rep in realm["clients"]:
        found = kc.call("GET", f"{admin}/clients?clientId={urllib.parse.quote(rep['clientId'])}")
        if found:
            clients[rep["clientId"]] = found[0]["id"]
            continue
        plan.append(f"клиент {rep['clientId']}")
        if apply:
            kc.call("POST", f"{admin}/clients", rep)
            clients[rep["clientId"]] = kc.call(
                "GET", f"{admin}/clients?clientId={rep['clientId']}"
            )[0]["id"]

    for user in realm["users"]:
        uid = _user_id(kc, admin, user, clients, plan, apply=apply)
        if uid is None:
            # Без --apply нового пользователя ещё нет: его роли тоже будут добавлены.
            plan.extend(f"роль {r} у {user['username']}" for r in user.get("realmRoles", []))
            continue
        have = {r["name"] for r in kc.call("GET", f"{admin}/users/{uid}/role-mappings/realm")}
        missing = [r for r in user.get("realmRoles", []) if r not in have]
        plan.extend(f"роль {r} у {user['username']}" for r in missing)
        if apply and missing:
            reps = [kc.call("GET", f"{admin}/roles/{r}") for r in missing]
            kc.call("POST", f"{admin}/users/{uid}/role-mappings/realm", reps)
        for client_id, roles in (user.get("clientRoles") or {}).items():
            ccid = kc.call("GET", f"{admin}/clients?clientId={client_id}")[0]["id"]
            have = {
                r["name"]
                for r in kc.call("GET", f"{admin}/users/{uid}/role-mappings/clients/{ccid}")
            }
            missing = [r for r in roles if r not in have]
            plan.extend(f"роль {client_id}/{r} у {user['username']}" for r in missing)
            if apply and missing:
                reps = [kc.call("GET", f"{admin}/clients/{ccid}/roles/{r}") for r in missing]
                kc.call("POST", f"{admin}/users/{uid}/role-mappings/clients/{ccid}", reps)
    return plan


def _user_id(
    kc: Keycloak,
    admin: str,
    user: dict[str, Any],
    clients: dict[str, str],
    plan: list[str],
    *,
    apply: bool,
) -> str | None:
    """Служебная учётная запись — у клиента; человек — по логину, нет — заводим."""
    if user.get("serviceAccountClientId"):
        cid = clients.get(user["serviceAccountClientId"])
        if cid is None:
            return None
        return str(kc.call("GET", f"{admin}/clients/{cid}/service-account-user")["id"])
    query = urllib.parse.urlencode({"exact": "true", "username": user["username"]})
    existing = kc.call("GET", f"{admin}/users?{query}")
    if existing:
        return str(existing[0]["id"])
    plan.append(f"пользователь {user['username']}")
    if not apply:
        return None
    rep = {k: v for k, v in user.items() if k not in {"realmRoles", "clientRoles"}}
    kc.call("POST", f"{admin}/users", rep)
    return str(kc.call("GET", f"{admin}/users?{query}")[0]["id"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="добавить, а не только показать")
    parser.add_argument(
        "--url", default=os.environ.get("KEYCLOAK_URL", "http://localhost:8080/auth")
    )
    args = parser.parse_args()
    realm = json.loads(REALM_FILE.read_text(encoding="utf-8"))
    try:
        kc = Keycloak(
            args.url,
            os.environ.get("KEYCLOAK_ADMIN_USER", "kcadmin"),
            os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "kcadmin-dev"),
        )
        plan = sync(kc, realm, apply=args.apply)
    except urllib.error.HTTPError as exc:
        print(f"Keycloak ответил {exc.code} на {exc.url}: {exc.read().decode(errors='replace')}")
        return 1
    except urllib.error.URLError as exc:
        print(f"Keycloak недоступен по адресу {args.url}: {exc.reason}")
        return 1
    if not plan:
        print("Keycloak совпадает с realm — добавлять нечего.")
        return 0
    print("Добавлено:" if args.apply else "Добавит (запустите с --apply):")
    for item in plan:
        print(" -", item)
    return 0


if __name__ == "__main__":
    sys.exit(main())
