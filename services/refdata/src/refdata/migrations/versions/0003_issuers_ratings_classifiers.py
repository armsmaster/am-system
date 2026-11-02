"""Эмитенты, рейтинги и классификаторы — данные для лимитов (design/09, часть 9.1).

У бумаг, загруженных раньше, эмитент и тип ISS появятся при ближайшей сверке с ISS:
ежедневной или по кнопке «Обновить из ISS».

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade() -> None:
    op.create_table(
        "issuer",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("iss_id", sa.Integer, nullable=False, unique=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("inn", sa.Text),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.add_column(
        "instrument",
        sa.Column("issuer_id", sa.Uuid, sa.ForeignKey("issuer.id", name="instrument_issuer_fk")),
    )
    op.add_column("instrument", sa.Column("iss_type", sa.Text))
    op.create_table(
        "rating",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column(
            "instrument_id", sa.Uuid, sa.ForeignKey("instrument.id", name="rating_instrument_fk")
        ),
        sa.Column("issuer_id", sa.Uuid, sa.ForeignKey("issuer.id", name="rating_issuer_fk")),
        sa.Column("action", sa.Text, nullable=False),
        sa.Column("agency", sa.Text),
        sa.Column("rating", sa.Text),
        sa.Column("grade", sa.Text),
        sa.Column("valid_from", sa.Date, nullable=False),
        sa.Column("comment", sa.Text),
        sa.Column("author", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_by", sa.Text),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_comment", sa.Text),
        sa.CheckConstraint("(instrument_id IS NULL) <> (issuer_id IS NULL)", name="rating_subject"),
        sa.CheckConstraint("action IN ('assigned', 'withdrawn', 'unrated')", name="rating_action"),
        sa.CheckConstraint(
            "(action = 'assigned') = (rating IS NOT NULL AND grade IS NOT NULL)",
            name="rating_value",
        ),
        sa.CheckConstraint("(action = 'unrated') = (agency IS NULL)", name="rating_agency"),
    )
    op.create_index("rating_instrument", "rating", ["instrument_id", "seq"])
    op.create_index("rating_issuer", "rating", ["issuer_id", "seq"])
    op.create_table(
        "classifier",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("target", sa.Text, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column("created_by", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("target IN ('instrument', 'issuer')", name="classifier_target"),
    )
    op.create_table(
        "classifier_value",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "classifier_id",
            sa.Uuid,
            sa.ForeignKey("classifier.id", name="classifier_value_classifier_fk"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("position", sa.Integer, nullable=False),
        sa.UniqueConstraint("classifier_id", "name", name="classifier_value_name"),
    )
    op.create_table(
        "classification",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column("seq", sa.BigInteger, sa.Identity(), nullable=False),
        sa.Column(
            "classifier_id",
            sa.Uuid,
            sa.ForeignKey("classifier.id", name="classification_classifier_fk"),
            nullable=False,
        ),
        sa.Column(
            "instrument_id",
            sa.Uuid,
            sa.ForeignKey("instrument.id", name="classification_instrument_fk"),
        ),
        sa.Column(
            "issuer_id", sa.Uuid, sa.ForeignKey("issuer.id", name="classification_issuer_fk")
        ),
        sa.Column(
            "value_id",
            sa.Uuid,
            sa.ForeignKey("classifier_value.id", name="classification_value_fk"),
            nullable=False,
        ),
        sa.Column("valid_from", sa.Date),
        sa.Column("comment", sa.Text),
        sa.Column("author", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_by", sa.Text),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancel_comment", sa.Text),
        sa.CheckConstraint(
            "(instrument_id IS NULL) <> (issuer_id IS NULL)", name="classification_subject"
        ),
    )
    op.create_index("classification_instrument", "classification", ["instrument_id", "seq"])
    op.create_index("classification_issuer", "classification", ["issuer_id", "seq"])
