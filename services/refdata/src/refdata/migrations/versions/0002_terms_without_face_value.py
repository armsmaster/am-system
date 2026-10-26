"""Номинал на дату — не условие выпуска (решение 63).

Версия хранила текущий номинал из ISS. Версия 1 действует с начала
обращения, поэтому у линкера и у облигации, которую загрузили после
амортизации, номинал на прошлые даты получался неверным. Поле убираем
из сохранённых версий и из последнего ответа ISS. Номинал на дату
ведут рыночные данные (шаг 3).

Миграции пишутся только вперёд: откат делается новой миграцией.
"""

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade() -> None:
    op.execute("UPDATE instrument_version SET params = params - 'face_value'")
    op.execute(
        "UPDATE instrument SET iss_snapshot = iss_snapshot - 'face_value'"
        " WHERE iss_snapshot IS NOT NULL"
    )
