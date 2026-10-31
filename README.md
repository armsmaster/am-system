# am-system

Система контроля позиции управляющей компании паевых фондов.

- Архитектура: [docs/architecture.md](docs/architecture.md)
- Решения по предметной области: [docs/decisions.md](docs/decisions.md)
- Открытые вопросы: [docs/open-questions.md](docs/open-questions.md)
- Дизайн шагов: [docs/design/](docs/design/)
- Блог о системе для инженеров и финансистов: [docs/blog/](docs/blog/README.md)

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

Если realm Keycloak менялся, пересоздайте данные: `make reset && make up`.
Keycloak импортирует realm только в пустую базу.

Шлюз следит за файлами маршрутов в `infra/traefik/dynamic/`, но на Docker
Desktop под Windows события изменения файлов до контейнера не доходят.
После правки маршрутов перезапустите шлюз: `docker compose restart gateway`.

Консоль Keycloak: <http://localhost:8080/auth/admin>
(`kcadmin` / `kcadmin-dev`). Панель шлюза: <http://localhost:8081>.

## Разработка фронтенда без пересборки образов

Окружение (`make up`) должно быть запущено — из него берутся Keycloak и API.

```bash
cd frontend
npm run dev:admin   # администрирование на :3001
npm run dev:audit   # журнал на :3002
npm run dev:refdata # справочник на :3003
npm run dev:marketdata # цены на :3004
npm run dev:booking    # сделки на :3005
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
libs/am-common/     общая библиотека: настройки, токены, БД, outbox, события, миграции
libs/am-quant/      расчёты по облигациям: доходность, метрики, спреды флоатеров и линкеров
services/Dockerfile общий образ Python-сервисов
services/portfolio/ портфели, фонды, счета, доступы
services/audit/     журнал действий пользователей
services/refdata/   справочник: бумаги из MOEX ISS, версии, торговый календарь
services/booking/   сделки с версиями, операции фонда и переводы, позиции по двум датам с лотами FIFO,
                    деньги, лестница расчётов, пересчёт задним числом, импорт сделок пакетом
services/marketdata/ цены от скрипта и из ISS, OHLC и DEMA, ручные цены, НКД и номинал на дату,
                    ставки, доходность и спред на момент цены
frontend/
  packages/platform/  контракт shell ↔ микрофронтенды, API-клиент
  packages/ui/        общие помощники интерфейса: загрузка данных, действия, ошибки
  packages/shell/     оболочка: вход, навигация, общие стили
  packages/mfe-admin/ администрирование: портфели, счета, доступы
  packages/mfe-audit/ журнал действий
  packages/mfe-refdata/ справочник: поиск и загрузка бумаг, карточка, календарь
  packages/mfe-marketdata/ цены: обзор с разбросом, карточка с графиком и расчётом, правило, лента скрипта
  packages/mfe-booking/ сделки: ввод с подсказкой НКД, карточка с версиями, позиции по двум датам,
                    операции фонда, лестница расчётов с дефицитами, импорт сделок из файла
docs/               архитектура, решения, вопросы, дизайн шагов, спецификации для скриптов, блог
```
