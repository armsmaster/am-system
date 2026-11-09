"""Настройки для виртуальной машины: без значений разработки, со своими секретами."""

from __future__ import annotations

import importlib.util
import json
import shutil
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "vm_config.py"
REALM = Path(__file__).resolve().parents[2] / "infra" / "keycloak" / "realms" / "am-realm.json"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("vm_config", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


vm = load()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Копия нужной части репозитория: скрипт читает только realm разработки."""
    target = tmp_path / "infra" / "keycloak" / "realms"
    target.mkdir(parents=True)
    shutil.copy(REALM, target / "am-realm.json")
    return tmp_path


def env(root: Path) -> dict[str, str]:
    lines = (root / ".env").read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", 1) for line in lines if line and not line.startswith("#"))


def test_env_and_realm_for_the_machine(root: Path) -> None:
    password = vm.build(
        "https://AM.example.ru/", "ops@example.ru", root, force=False, today=date(2026, 11, 9)
    )

    values = env(root)
    assert {
        k: values[k]
        for k in ("PUBLIC_URL", "DOMAIN", "COMPOSE_PROFILES", "GATEWAY_BIND", "KEYCLOAK_MODE")
    } == {
        "PUBLIC_URL": "https://am.example.ru",
        "DOMAIN": "am.example.ru",
        "COMPOSE_PROFILES": "tls",
        "GATEWAY_BIND": "127.0.0.1",
        "KEYCLOAK_MODE": "start",
    }
    # Ни одного значения разработки; все пароли и секреты разные.
    secret_names = [*vm.PASSWORDS, *vm.CLIENT_SECRETS.values()]
    secrets = [values[name] for name in secret_names]
    assert all(len(s) >= 32 and "dev" not in s for s in secrets)
    assert len(set(secrets)) == len(secrets)

    realm = json.loads((root / vm.VM_REALM_DIR / "am-realm.json").read_text(encoding="utf-8"))
    clients = {c["clientId"]: c for c in realm["clients"]}
    assert clients["am-web"]["redirectUris"] == ["https://am.example.ru/*"]
    assert clients["am-web"]["attributes"]["post.logout.redirect.uris"] == "https://am.example.ru/*"
    # Секреты в realm — те же, что получат сервисы из .env.
    assert {cid: clients[cid]["secret"] for cid in vm.CLIENT_SECRETS} == {
        cid: values[name] for cid, name in vm.CLIENT_SECRETS.items()
    }
    # Из людей — один администратор с временным паролем; служебные учётные записи на месте.
    people = [u for u in realm["users"] if not u.get("serviceAccountClientId")]
    assert [(u["username"], u["realmRoles"], u["credentials"]) for u in people] == [
        ("admin", ["admin"], [{"type": "password", "value": password, "temporary": True}])
    ]
    accounts = {
        u["serviceAccountClientId"] for u in realm["users"] if u.get("serviceAccountClientId")
    }
    assert accounts == {
        "am-portfolio",
        "am-marketdata",
        "am-booking",
        "am-reconciliation",
        "am-limits",
    } | {
        "am-price-feed",
        "am-trade-feed",
        "am-statement-feed",
    }
    assert (people[0]["email"], people[0]["emailVerified"]) == ("ops@example.ru", True)
    assert people[0]["requiredActions"] == ["UPDATE_PASSWORD"]
    assert realm["sslRequired"] == "external"
    assert "admin-dev" not in json.dumps(realm)


def test_does_not_overwrite_without_force(root: Path) -> None:
    vm.build("am.example.ru", "ops@example.ru", root, force=False, today=date(2026, 11, 9))
    first = env(root)["POSTGRES_PASSWORD"]

    with pytest.raises(vm.ConfigError, match=r"Уже есть \.env"):
        vm.build("am.example.ru", "ops@example.ru", root, force=False, today=date(2026, 11, 9))
    vm.build("am.example.ru", "ops@example.ru", root, force=True, today=date(2026, 11, 9))
    assert env(root)["POSTGRES_PASSWORD"] != first


@pytest.mark.parametrize("domain", ["", "am.example.ru/app"])
def test_domain_is_a_bare_name(root: Path, domain: str) -> None:
    with pytest.raises(vm.ConfigError, match="Домен"):
        vm.build(domain, "ops@example.ru", root, force=False, today=date(2026, 11, 9))


def test_realm_without_expected_clients(root: Path) -> None:
    path = root / "infra" / "keycloak" / "realms" / "am-realm.json"
    realm = json.loads(path.read_text(encoding="utf-8"))
    realm["clients"] = [c for c in realm["clients"] if c["clientId"] != "am-limits"]
    path.write_text(json.dumps(realm), encoding="utf-8")

    with pytest.raises(vm.ConfigError, match="am-limits"):
        vm.build("am.example.ru", "ops@example.ru", root, force=False, today=date(2026, 11, 9))


def test_command_line(
    root: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(vm, "ROOT", root)

    assert vm.main(["--domain", "am.example.ru", "--email", "ops@example.ru"]) == 0
    assert "Вход в приложение: admin" in capsys.readouterr().out
    assert vm.main(["--domain", "am.example.ru", "--email", "ops@example.ru"]) == 1
    assert "Ошибка: Уже есть" in capsys.readouterr().err
