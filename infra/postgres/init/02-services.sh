#!/bin/sh
# Роли и схемы сервисов в базе am: у каждого сервиса своя схема и своя роль.
#
# Скрипт идемпотентный: недостающее заводит, существующее не трогает. Postgres
# выполняет его при создании пустого тома (после 01-databases.sh), а сервис
# db-setup в compose — при каждом запуске: так новый сервис получает схему и в
# уже работающей базе, без `make reset`.
#
# Новый сервис — новое слово в SERVICES.
#
# Без `set -u`: см. 01-databases.sh.
set -e

SERVICES="portfolio refdata marketdata booking limits audit reconciliation identity"

for service in $SERVICES; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname am <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'svc_${service}') THEN
    CREATE ROLE svc_${service} LOGIN PASSWORD '${SERVICES_DB_PASSWORD}';
  END IF;
END
\$\$;
GRANT CONNECT ON DATABASE am TO svc_${service};
CREATE SCHEMA IF NOT EXISTS ${service} AUTHORIZATION svc_${service};
SQL
done
