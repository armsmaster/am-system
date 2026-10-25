# am-system

Система контроля позиции управляющей компании паевых фондов.

- Архитектура: [docs/architecture.md](docs/architecture.md)
- Решения по предметной области: [docs/decisions.md](docs/decisions.md)
- Открытые вопросы: [docs/open-questions.md](docs/open-questions.md)

## Что нужно установить

- Docker Desktop (или Docker Engine с compose)
- [uv](https://docs.astral.sh/uv/) — окружение Python 3.13
- Node.js 24 и npm
- GNU Make (в Git Bash на Windows есть)

## Запуск

```bash
cp .env.example .env   # необязательно: без .env берутся те же значения
make up
```

Приложение: <http://localhost:8080>. Первый запуск дольше: Keycloak
импортирует realm, образы собираются.

Пользователи для разработки (только локально, задаются в
`infra/keycloak/realms/am-realm.json`):

| Логин | Пароль | Роль |
|---|---|---|
| `admin` | `admin-dev` | администратор |
| `trader` | `trader-dev` | трейдинг |
| `ops` | `ops-dev` | сопровождение |

Консоль Keycloak: <http://localhost:8080/auth/admin>
(`kcadmin` / `kcadmin-dev`). Панель шлюза: <http://localhost:8081>.

## Разработка фронтенда без пересборки образов

Окружение (`make up`) должно быть запущено — из него берутся Keycloak и API.

```bash
cd frontend
npm run dev:admin   # микрофронтенд на :3001
npm run dev:shell   # shell на :3000
```

Открыть <http://localhost:3000>.

## Проверки

```bash
make check       # линтеры, типы, все тесты с покрытием — как в CI
make test-unit   # быстрые тесты бэкенда без контейнеров
```

Покрытие тестами — 100% строк и ветвей; CI не пропускает изменения ниже
порога. Из покрытия исключены только точки входа фронтенда
(`index.ts`, `bootstrap.tsx`) — это связка со сборщиком.

## Структура

```
compose.yaml        локальное окружение
infra/              Postgres (init), Keycloak (realm), Traefik (маршруты)
libs/am-common/     общая библиотека сервисов: настройки, токены, БД
services/portfolio/ сервис портфелей (пока заготовка: /me, пробы)
frontend/
  packages/platform/  контракт shell ↔ микрофронтенды
  packages/shell/     оболочка: вход, навигация
  packages/mfe-admin/ микрофронтенд администрирования
docs/               архитектура, решения, вопросы
```
