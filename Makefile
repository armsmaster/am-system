# Основные команды разработки. Работают в Git Bash (Windows), Linux и macOS.

.PHONY: up down reset logs install lint test test-unit check py-lint py-types py-test fe-lint fe-test fe-build

## Локальное окружение
up:          ## поднять всё окружение
	docker compose up -d --build

down:        ## остановить (данные сохраняются)
	docker compose down

reset:       ## остановить и удалить данные (Postgres, Redis)
	docker compose down -v

logs:
	docker compose logs -f

## Зависимости
install:
	uv sync
	cd frontend && npm install

## Проверки
py-lint: py-types
	uv run ruff check .
	uv run ruff format --check .

py-types:    ## mypy по каждому пакету отдельно
	@for pkg in libs/*/ services/*/; do echo "mypy $$pkg"; uv run mypy $${pkg}src $${pkg}tests || exit 1; done
	uv run mypy conftest.py

py-test:     ## все тесты бэкенда с покрытием (нужен Docker)
	uv run pytest --cov

test-unit:   ## быстрые тесты бэкенда без контейнеров
	uv run pytest -m "not integration"

fe-lint:
	cd frontend && npm run typecheck

fe-test:
	cd frontend && npm test

fe-build:
	cd frontend && npm run build

lint: py-lint fe-lint
test: py-test fe-test
check: lint test     ## то же, что проверяет CI
