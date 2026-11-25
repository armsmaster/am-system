#!/bin/sh
# Первичная настройка PostgreSQL для локальной разработки.
# Выполняется один раз, при создании пустого тома с данными.
#
# - база keycloak — для Keycloak;
# - база am — для сервисов: у каждого сервиса своя схема и своя роль,
#   и роль не видит чужие схемы. Роли и схемы заводит 02-services.sh.
#
# В облаке то же самое делается при подготовке управляемой БД.
#
# Без `set -u`: если у файла нет флага исполнения (типично для томов
# на Windows), entrypoint Postgres выполняет его в своём же shell.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE keycloak LOGIN PASSWORD '${KEYCLOAK_DB_PASSWORD}';
CREATE DATABASE keycloak OWNER keycloak;
CREATE DATABASE am;
-- По умолчанию к новой базе может подключиться любая роль — запрещаем.
REVOKE ALL ON DATABASE keycloak FROM PUBLIC;
REVOKE ALL ON DATABASE am FROM PUBLIC;
SQL

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname am <<SQL
REVOKE ALL ON SCHEMA public FROM PUBLIC;
SQL
