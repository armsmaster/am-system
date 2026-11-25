"""Обновление уже работающей ВМ: .env и Keycloak догоняют текущий realm."""

from __future__ import annotations

import copy
import importlib
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
REALM = SCRIPTS.parent / "infra" / "keycloak" / "realms" / "am-realm.json"
TODAY = date(2026, 11, 25)


def load(name: str) -> ModuleType:
    # Скрипт импортирует соседей по имени — как при запуске python3 scripts/vm_upgrade.py,
    # поэтому и тест берёт их тем же путём: один модуль vm_config на двоих.
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    return importlib.import_module(name)


vm = load("vm_config")
up = load("vm_upgrade")
DEV = json.loads(REALM.read_text(encoding="utf-8"))


class OldMachine:
    """Keycloak машины, поднятой до решения 170: нет am-identity, офлайн-сессий и политики."""

    def __init__(self) -> None:
        self.settings: dict[str, Any] = {
            "realm": "am",
            "offlineSessionIdleTimeout": 2592000,
            "offlineSessionMaxLifespanEnabled": False,
            "offlineSessionMaxLifespan": 5184000,
        }
        # Как по умолчанию в Keycloak: «проверка профиля» включена.
        self.actions = [
            {**a, "enabled": a["enabled"] or a["alias"] == "VERIFY_PROFILE"}
            for a in DEV["requiredActions"]
        ]
        self.roles = {r["name"] for r in DEV["roles"]["realm"]} | {"offline_access"}
        self.clients: dict[str, dict[str, Any]] = {}
        self.users: dict[str, dict[str, Any]] = {}
        self.realm_roles: dict[str, set[str]] = {}
        self.client_roles: dict[tuple[str, str], set[str]] = {}
        # Встроенный клиент Keycloak: его роли — права на пользователей.
        self._add_client({"clientId": "realm-management"})
        for client in DEV["clients"]:
            if client["clientId"] != "am-identity":
                old = {"secret": "old-random", "directAccessGrantsEnabled": False}
                self._add_client({**client, **old})
        # Права служебных учётных записей — как при импорте прежнего realm.
        for user in DEV["users"]:
            client_id = user.get("serviceAccountClientId")
            if client_id and client_id != "am-identity":
                self.realm_roles[f"sa-{client_id}"] = set(user.get("realmRoles", []))
                for owner, roles in (user.get("clientRoles") or {}).items():
                    self.client_roles[(f"sa-{client_id}", owner)] = set(roles)
        # Администратор из realm ВМ — без ролей по умолчанию; заведённый в консоли — с ними.
        self._add_user("u-admin", "admin", {"admin"})
        self._add_user("u-ivan", "ivan", {"trading", "default-roles-am"})
        self.calls: list[tuple[str, str]] = []

    def snapshot(self) -> str:
        state = (self.settings, self.actions, self.roles, self.clients, self.realm_roles)
        return json.dumps((state, sorted(map(str, self.client_roles.items()))), default=sorted)

    def _add_client(self, rep: dict[str, Any]) -> None:
        cid = f"c-{rep['clientId']}"
        self.clients[rep["clientId"]] = {**copy.deepcopy(rep), "id": cid}
        if rep.get("serviceAccountsEnabled"):
            self._add_user(f"sa-{rep['clientId']}", f"service-account-{rep['clientId']}", set())

    def _add_user(self, uid: str, username: str, roles: set[str]) -> None:
        self.users[uid] = {"id": uid, "username": username}
        self.realm_roles[uid] = set(roles)

    def call(self, method: str, path: str, body: Any = None) -> Any:  # noqa: PLR0911, PLR0912
        self.calls.append((method, path))
        url = urlsplit(path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        rest = url.path.removeprefix("/admin/realms/am")
        if rest == "":
            if method == "PUT":
                self.settings.update(body)
                return None
            return dict(self.settings)
        if rest == "/authentication/required-actions":
            return copy.deepcopy(self.actions)
        if m := re.fullmatch(r"/authentication/required-actions/(.+)", rest):
            next(a for a in self.actions if a["alias"] == m[1])["enabled"] = body["enabled"]
            return None
        if rest == "/roles":
            if method == "POST":
                self.roles.add(body["name"])
                return None
            return [{"name": n} for n in sorted(self.roles)]
        if m := re.fullmatch(r"/roles/([^/]+)", rest):
            return {"id": f"r-{m[1]}", "name": m[1]}
        if rest == "/clients":
            if method == "POST":
                self._add_client(body)
                return None
            found = self.clients.get(query["clientId"])
            return [copy.deepcopy(found)] if found else []
        if m := re.fullmatch(r"/clients/c-([^/]+)(/.*)?", rest):
            client = self.clients[m[1]]
            if m[2] == "/service-account-user":
                return {"id": f"sa-{m[1]}"}
            if m[2] and m[2].startswith("/roles/"):
                return {"name": m[2].removeprefix("/roles/")}
            client.update({k: v for k, v in body.items() if k != "id"})
            return None
        if rest == "/users":
            users = list(self.users.values())
            if "username" in query:
                users = [u for u in users if u["username"] == query["username"]]
            return copy.deepcopy(users)
        m = re.fullmatch(r"/users/([^/]+)/role-mappings/(realm|realm/composite|clients/c-.+)", rest)
        assert m is not None, f"unexpected {method} {path}"
        uid, kind = m[1], m[2]
        if kind == "realm/composite":
            names = set(self.realm_roles[uid])
            if "default-roles-am" in names:
                names.add("offline_access")
            return [{"name": n} for n in sorted(names)]
        target = (
            self.realm_roles[uid]
            if kind == "realm"
            else self.client_roles.setdefault((uid, kind.removeprefix("clients/c-")), set())
        )
        if method == "POST":
            target.update(r["name"] for r in body)
            return None
        return [{"name": n} for n in sorted(target)]


@pytest.fixture
def env_file(tmp_path: Path) -> Path:
    """.env машины, собранной до решения 170: без секрета am-identity."""
    realms = tmp_path / "infra" / "keycloak" / "realms"
    realms.mkdir(parents=True)
    shutil.copy(REALM, realms / "am-realm.json")
    vm.build("am.example.ru", "ops@example.ru", tmp_path, force=False, today=date(2026, 11, 9))
    path = tmp_path / ".env"
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text(
        "\n".join(line for line in lines if not line.startswith("IDENTITY_KEYCLOAK_SECRET")) + "\n",
        encoding="utf-8",
    )
    return path


def run(env_file: Path, machine: OldMachine, *, apply: bool, url: str | None = None) -> list[str]:
    connected: list[tuple[str, str, str]] = []

    def connect(base: str, user: str, password: str) -> OldMachine:
        connected.append((base, user, password))
        return machine

    plan: list[str] = up.upgrade(env_file, connect, url=url, apply=apply, today=TODAY)
    env = up.read_env(env_file)
    base = url or "http://127.0.0.1:8080/auth"
    assert connected == [(base, "kcadmin", env["KEYCLOAK_ADMIN_PASSWORD"])]
    return plan


def test_dry_run_shows_the_steps_and_changes_nothing(env_file: Path) -> None:
    machine = OldMachine()
    before_env, before_kc = env_file.read_text(encoding="utf-8"), machine.snapshot()

    plan = run(env_file, machine, apply=False)

    assert plan == [
        ".env: IDENTITY_KEYCLOAK_SECRET — новый случайный секрет",
        "настройка realm offlineSessionIdleTimeout = 604800",
        "настройка realm offlineSessionMaxLifespanEnabled = True",
        "настройка realm offlineSessionMaxLifespan = 2592000",
        "настройка realm passwordPolicy = length(10) and notUsername",
        "обязательное действие VERIFY_PROFILE: выключить",
        "клиент am-web: directAccessGrantsEnabled = True",
        "клиент am-identity",
        "роль offline_access у admin",
    ]
    assert env_file.read_text(encoding="utf-8") == before_env
    assert machine.snapshot() == before_kc


def test_apply_brings_the_machine_to_the_realm_once(env_file: Path) -> None:
    machine = OldMachine()
    old_lines = env_file.read_text(encoding="utf-8").splitlines()

    run(env_file, machine, apply=True)

    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines[: len(old_lines)] == old_lines
    secret = up.read_env(env_file)["IDENTITY_KEYCLOAK_SECRET"]
    assert len(secret) >= 32
    assert lines[-2].startswith("# Дописано scripts/vm_upgrade.py 2026-11-25")
    identity = machine.clients["am-identity"]
    assert identity["secret"] == secret
    assert identity["serviceAccountsEnabled"] is True
    assert machine.client_roles[("sa-am-identity", "realm-management")] == {
        "view-users",
        "query-users",
        "manage-users",
    }
    assert machine.clients["am-web"]["directAccessGrantsEnabled"] is True
    # Секреты прежних клиентов — как были: сервисы машины с ними работают.
    assert machine.clients["am-portfolio"]["secret"] == "old-random"
    assert machine.settings["passwordPolicy"] == "length(10) and notUsername"
    assert next(a for a in machine.actions if a["alias"] == "VERIFY_PROFILE")["enabled"] is False
    assert "offline_access" in machine.realm_roles["u-admin"]
    assert "offline_access" not in machine.realm_roles["u-ivan"]  # есть через роли по умолчанию
    # Людей скрипт не заводит.
    assert {u["username"] for u in machine.users.values() if not u["id"].startswith("sa-")} == {
        "admin",
        "ivan",
    }

    assert run(env_file, machine, apply=False) == []
    assert run(env_file, machine, apply=True) == []


def test_keycloak_address_from_gateway_port_or_option(env_file: Path) -> None:
    with env_file.open("a", encoding="utf-8") as file:
        file.write("GATEWAY_PORT=9090")
    connected: list[str] = []

    def connect(base: str, _user: str, _password: str) -> OldMachine:
        connected.append(base)
        return OldMachine()

    up.upgrade(env_file, connect, url=None, apply=False, today=TODAY)
    up.upgrade(env_file, connect, url="http://kc:8080/auth", apply=False, today=TODAY)

    assert connected == ["http://127.0.0.1:9090/auth", "http://kc:8080/auth"]


def test_new_secrets_start_on_a_new_line(env_file: Path) -> None:
    text = env_file.read_text(encoding="utf-8").rstrip("\n")
    env_file.write_text(text, encoding="utf-8")

    up.append_secrets(env_file, ["A_SECRET"], TODAY)

    assert up.read_env(env_file)["A_SECRET"]
    assert env_file.read_text(encoding="utf-8").startswith(text + "\n\n# Дописано")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("KEYCLOAK_MODE=start", "KEYCLOAK_MODE=start-dev"), "не .env виртуальной машины"),
        (("PUBLIC_URL=https://", "PUBLIC_URL=http://"), "не .env виртуальной машины"),
        (("KEYCLOAK_ADMIN_USER=kcadmin", "KEYCLOAK_ADMIN_USER="), "нет KEYCLOAK_ADMIN_USER"),
    ],
)
def test_refuses_anything_but_a_machine(
    env_file: Path, change: tuple[str, str], message: str
) -> None:
    env_file.write_text(env_file.read_text(encoding="utf-8").replace(*change), encoding="utf-8")

    with pytest.raises(vm.ConfigError, match=message):
        up.upgrade(env_file, lambda *_: OldMachine(), url=None, apply=True, today=TODAY)


def test_refuses_a_development_secret(env_file: Path) -> None:
    with env_file.open("a", encoding="utf-8") as file:
        file.write("IDENTITY_KEYCLOAK_SECRET=identity-dev-secret\n")
    machine = OldMachine()

    with pytest.raises(vm.ConfigError, match="секрет разработки"):
        up.upgrade(env_file, lambda *_: machine, url=None, apply=True, today=TODAY)
    assert "am-identity" not in machine.clients


def test_main_explains_problems(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert up.main(["--env", str(tmp_path / "missing.env")]) == 1
    assert "Нет " in capsys.readouterr().err


def test_main_reports_unreachable_keycloak(
    env_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert up.main(["--env", str(env_file), "--url", "http://127.0.0.1:9/auth"]) == 1
    assert "Keycloak недоступен" in capsys.readouterr().out
