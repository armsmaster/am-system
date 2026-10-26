"""env.py тестовых миграций — такой же, как у любого сервиса."""

from sqlalchemy import MetaData

from am_common.migrations import run_env

run_env(MetaData())
