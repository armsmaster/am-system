#!/bin/sh
# Первичная настройка PostgreSQL для локальной разработки.
# Выполняется один раз, при создании пустого тома с данными.
#
# - база keycloak — для Keycloak;
# - база am — для сервисов: у каждого сервиса своя схема и своя роль,
#   и роль не видит чужие схемы.
#
# В облаке то же самое делается при подготовке управляемой БД.
#
# Без `set -u`: если у файла нет флага исполнения (типично для томов
# на Windows), entrypoint Postgres выполняет его в своём же shell.
set -e

SERVICES="portfolio refdata marketdata booking limits audit reconciliation"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE keycloak LOGIN PASSWORD '${KEYCLOAK_DB_PASSWORD}';
CREATE DATABASE keycloak OWNER keycloak;
CREATE DATABASE am;
-- По умолчанию к новой базе может подключиться любая роль — запрещаем.
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
REVOKE ALL ON DATABASE am FROM PUBLIC;
SQL

for service in $SERVICES; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname am <<SQL
CREATE ROLE svc_${service} LOGIN PASSWORD '${SERVICES_DB_PASSWORD}';
GRANT CONNECT ON DATABASE am TO svc_${service};
CREATE SCHEMA ${service} AUTHORIZATION svc_${service};
SQL
done

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname am <<SQL
REVOKE ALL ON SCHEMA public FROM PUBLIC;
SQL
