"""Журнал действий: таблица только для дозаписи.

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None


def upgrade() -> None:
    op.create_table(
        "audit_event",
        sa.Column("event_id", sa.Uuid, primary_key=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service", sa.Text, nullable=False),
        sa.Column("type", sa.Text, nullable=False),
        sa.Column("actor_subject", sa.Text, nullable=False),
        sa.Column("actor_username", sa.Text, nullable=False),
        sa.Column("entity_type", sa.Text, nullable=False),
        sa.Column("entity_id", sa.Text, nullable=False),
        sa.Column("data", postgresql.JSONB, nullable=False),
        sa.Column("comment", sa.Text),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("audit_event_by_time", "audit_event", ["occurred_at", "event_id"])
    op.create_index(
        "audit_event_by_entity", "audit_event", ["entity_type", "entity_id", "occurred_at"]
    )
    op.create_index("audit_event_by_actor", "audit_event", ["actor_subject", "occurred_at"])

    # Журнал нельзя исправить задним числом: любые UPDATE, DELETE и TRUNCATE
    # отклоняются самой базой, кто бы их ни выполнял.
    op.execute(
        """
        CREATE FUNCTION forbid_audit_change() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_event is append-only: % is not allowed', TG_OP;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_event_append_only BEFORE UPDATE OR DELETE ON audit_event "
        "FOR EACH ROW EXECUTE FUNCTION forbid_audit_change()"
    )
    op.execute(
        "CREATE TRIGGER audit_event_no_truncate BEFORE TRUNCATE ON audit_event "
        "FOR EACH STATEMENT EXECUTE FUNCTION forbid_audit_change()"
    )
