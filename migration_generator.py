"""
Генератор файлов миграций.
Замена alembic revision -m "описание".

Создаёт файл с timestamp-based revision и down_revision = None.
"""

import os
import secrets
import subprocess
from datetime import datetime


def _get_git_user() -> str:
    """Получает имя пользователя из git config."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


TEMPLATE = '''\
"""{description}

Revision ID: {revision}
Create Date: {create_date}
Created By: {created_by}
"""
from alembic import op
import sqlalchemy as sa


revision = {revision_repr}
down_revision = None
depends_on = {depends_on_repr}
description = {description_repr}
created_by = {created_by_repr}


def upgrade():
    pass


def downgrade():
    pass
'''


def create_migration(
    description: str,
    versions_dir: str,
    depends_on: str = None,
) -> str:
    """
    Создаёт новый файл миграции.

    Аргументы:
        description  — человекочитаемое описание (напр. "add email to users")
        versions_dir — папка, куда положить файл (versions_v2/)
        depends_on   — version миграции-зависимости (опционально)

    Возвращает путь к созданному файлу.
    """
    os.makedirs(versions_dir, exist_ok=True)

    now = datetime.utcnow()
    suffix = secrets.token_hex(2)  # 4 символа: a3f1
    revision = now.strftime("%Y%m%d_%H%M%S") + "_" + suffix

    # Slug из описания для имени файла
    slug = description.lower().replace(" ", "_").replace("-", "_")
    slug = "".join(c for c in slug if c.isalnum() or c == "_")
    slug = slug[:50]

    filename = "{}_{}.py".format(revision, slug)
    filepath = os.path.join(versions_dir, filename)

    created_by = _get_git_user()

    content = TEMPLATE.format(
        description=description,
        revision=revision,
        revision_repr=repr(revision),
        create_date=now.strftime("%Y-%m-%d %H:%M:%S.%f"),
        depends_on_repr=repr(depends_on),
        description_repr=repr(description),
        created_by=created_by,
        created_by_repr=repr(created_by),
    )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    print("Создана миграция: {}".format(filepath))
    return filepath
