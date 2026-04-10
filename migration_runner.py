"""
Бесконфликтный runner миграций.
Замена стандартного alembic upgrade head.

Принцип работы:
- Каждая миграция — независимый файл (down_revision = None)
- Порядок определяется timestamp в имени файла
- Таблица schema_migrations хранит все применённые версии
- pending = файлы - applied, сортировка по timestamp, применение по порядку

Совместимость: SQLAlchemy 1.4+, PostgreSQL, Greenplum
"""
import hashlib
import importlib.util
import logging
import os
import re
import time

from sqlalchemy import text, create_engine, pool
from sqlalchemy.engine import Connection, Engine


logger = logging.getLogger("migration_runner")

# ──────────────────────────────────────────────
# Конфигурация — адаптируйте под свой проект
# ──────────────────────────────────────────────

SCHEMA_MIGRATIONS_TABLE = "schema_migrations"
TABLE_SCHEMA = "public"

# Для Greenplum: DISTRIBUTED BY. Для PostgreSQL: оставьте пустой строкой.
DISTRIBUTED_BY = "DISTRIBUTED BY (version)"
# DISTRIBUTED_BY = ""  # для обычного PostgreSQL

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.{table} (
    version       VARCHAR(64)  PRIMARY KEY,
    description   TEXT,
    applied_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
    duration_ms   INTEGER,
    checksum      VARCHAR(64)
) {distributed_by}
""".format(
    schema=TABLE_SCHEMA,
    table=SCHEMA_MIGRATIONS_TABLE,
    distributed_by=DISTRIBUTED_BY,
)


# ──────────────────────────────────────────────
# Таблица schema_migrations
# ──────────────────────────────────────────────

def ensure_table(conn: Connection):
    """Создаёт таблицу schema_migrations, если её нет."""
    conn.execute(text(SCHEMA_MIGRATIONS_DDL))


# ──────────────────────────────────────────────
# Работа с файлами миграций
# ──────────────────────────────────────────────

def extract_revision_from_file(filepath: str):
    """
    Извлекает revision из файла миграции через regex.
    Быстрее чем importlib — используется для baseline (2500+ файлов).
    """
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read(2000)
    match = re.search(r"^revision\s*=\s*['\"]([^'\"]+)['\"]", content, re.MULTILINE)
    return match.group(1) if match else None


def file_checksum(filepath: str) -> str:
    """SHA-256 файла (первые 16 символов) — для обнаружения изменений после применения."""
    with open(filepath, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


def load_migration_module(filepath: str):
    """Загружает .py файл миграции как Python-модуль."""
    module_name = "migration_{}".format(os.path.basename(filepath).replace(".py", ""))
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scan_migration_files(versions_dir: str) -> list:
    """
    Сканирует директорию с новыми миграциями.
    Возвращает список словарей, отсортированный по имени файла (= по timestamp).
    """
    if not os.path.isdir(versions_dir):
        return []

    migrations = []

    for filename in sorted(os.listdir(versions_dir)):
        if not filename.endswith(".py") or filename.startswith("__") or filename.startswith("."):
            continue

        filepath = os.path.join(versions_dir, filename)
        if not os.path.isfile(filepath):
            continue

        module = load_migration_module(filepath)

        if not hasattr(module, "upgrade"):
            continue

        migrations.append({
            "version": module.revision,
            "description": getattr(module, "description", None) or filename,
            "depends_on": getattr(module, "depends_on", None),
            "filepath": filepath,
            "filename": filename,
            "module": module,
            "checksum": file_checksum(filepath),
        })

    migrations.sort(key=lambda m: m["filename"])
    return migrations


# ──────────────────────────────────────────────
# Baseline: запись существующих миграций
# ──────────────────────────────────────────────

def fill_baseline(conn: Connection, legacy_dir: str):
    """
    Заполняет schema_migrations всеми существующими legacy-миграциями как applied.
    Вызывается только если таблица пустая (первый запуск).
    Использует regex для извлечения revision (без загрузки модулей).
    """
    count = conn.execute(
        text("SELECT COUNT(*) FROM {}.{}".format(TABLE_SCHEMA, SCHEMA_MIGRATIONS_TABLE))
    ).scalar()

    if count > 0:
        return

    logger.info("таблица schema_migrations пуста — заполняю baseline из legacy-миграций...")

    values = []
    for root, _dirs, files in os.walk(legacy_dir):
        for filename in files:
            if not filename.endswith(".py") or filename.startswith("__"):
                continue

            filepath = os.path.join(root, filename)
            revision = extract_revision_from_file(filepath)
            if revision:
                values.append({"ver": revision, "desc": "legacy: {}".format(filename)})

    if not values:
        logger.warning("Не найдено legacy-миграций для baseline")
        return

    conn.execute(
        text(
            "INSERT INTO {}.{} (version, description) VALUES (:ver, :desc)".format(
                TABLE_SCHEMA, SCHEMA_MIGRATIONS_TABLE
            )
        ),
        values
    )

    logger.info("Baseline: записано {} legacy-миграций".format(len(values)))


# ──────────────────────────────────────────────
# Чтение состояния
# ──────────────────────────────────────────────

def get_applied_versions(conn: Connection) -> set:
    """Возвращает множество всех применённых версий из schema_migrations."""
    rows = conn.execute(
        text("SELECT version FROM {}.{}".format(TABLE_SCHEMA, SCHEMA_MIGRATIONS_TABLE))
    ).fetchall()
    return {row[0] for row in rows}


def get_applied_checksums(conn: Connection) -> dict:
    """Возвращает {version: checksum} для всех applied-миграций с непустым checksum."""
    rows = conn.execute(
        text("SELECT version, checksum FROM {}.{} WHERE checksum IS NOT NULL".format(
            TABLE_SCHEMA, SCHEMA_MIGRATIONS_TABLE
        ))
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def check_checksums(conn: Connection, migrations: list) -> list:
    """
    Сравнивает checksum файлов с checksum в таблице.
    Возвращает список миграций, у которых checksum изменился.
    """
    stored = get_applied_checksums(conn)
    tampered = []

    for m in migrations:
        version = m["version"]
        if version not in stored:
            continue  # не applied или legacy (checksum = NULL)
        if stored[version] != m["checksum"]:
            tampered.append(m)

    return tampered


def update_checksum(conn: Connection, version: str, new_checksum: str):
    """Обновляет checksum конкретной миграции в таблице."""
    conn.execute(
        text("UPDATE {}.{} SET checksum = :cs WHERE version = :ver".format(
            TABLE_SCHEMA, SCHEMA_MIGRATIONS_TABLE
        )),
        {"cs": new_checksum, "ver": version},
    )


# ──────────────────────────────────────────────
# Проверки
# ──────────────────────────────────────────────

def check_depends_on(migration: dict, known_versions: set):
    """
    Проверяет, что все зависимости миграции будут выполнены до неё.
    known_versions — это applied + pending-миграции с меньшим timestamp.
    """
    depends = migration["depends_on"]
    if not depends:
        return

    if isinstance(depends, str):
        depends = [depends]

    missing = [d for d in depends if d not in known_versions]
    if missing:
        raise RuntimeError(
            "Миграция {} ({}) зависит от {}, но они не найдены "
            "ни в applied, ни в pending-миграциях с более ранним timestamp.".format(
                migration["version"], migration["filename"], missing
            )
        )


# ──────────────────────────────────────────────
# Основной runner
# ──────────────────────────────────────────────

def _log(message: str, verbose: bool):
    """Выводит сообщение в stdout (если verbose) и в logger."""
    logger.info(message)
    if verbose:
        print(message)


def run_upgrade(
    engine: Engine,
    new_dir: str,
    legacy_dir: str = None,
    db_role: str = None,
    search_path: str = None,
    verbose: bool = False,
):
    """
    Главная функция: применяет все pending-миграции.

    Подход A: одна транзакция, один LOCK на всё время.
    Если любая миграция упадёт — ROLLBACK всего.
    При повторном запуске — pending будет тот же.

    Аргументы:
        engine      — SQLAlchemy Engine
        new_dir     — папка с новыми миграциями (versions_v2/)
        legacy_dir  — папка со старыми миграциями для baseline (опционально)
        db_role     — роль БД для SET ROLE (опционально)
        search_path — схема для SET search_path (опционально)
        verbose     — выводить прогресс в stdout
    """

    def _set_role(conn):
        if db_role:
            conn.execute(text("SET ROLE {}".format(db_role)))

    # ── Фаза 1: ensure table + baseline ──
    _log("Проверяю таблицу schema_migrations...", verbose)
    with engine.begin() as conn:
        _set_role(conn)
        ensure_table(conn)

    if legacy_dir:
        with engine.begin() as conn:
            _set_role(conn)
            fill_baseline(conn, legacy_dir)

    # ── Фаза 2: apply migrations (одна транзакция с LOCK) ──
    with engine.begin() as conn:
        _set_role(conn)

        conn.execute(
            text("LOCK TABLE {}.{} IN ACCESS EXCLUSIVE MODE".format(
                TABLE_SCHEMA, SCHEMA_MIGRATIONS_TABLE
            ))
        )

        if search_path:
            conn.execute(text("SET search_path TO {}".format(search_path)))

        applied = get_applied_versions(conn)
        _log("Applied миграций в БД: {}".format(len(applied)), verbose)

        all_new = scan_migration_files(new_dir)

        # Проверяем checksum applied-миграций
        tampered = check_checksums(conn, all_new)
        if tampered:
            _log("", verbose)
            _log("WARNING: обнаружены изменения в уже applied миграциях:", verbose)
            for m in tampered:
                _log("  - {} ({})".format(m["version"], m["description"]), verbose)
                _log("    Файл был изменён после применения.", verbose)
                _log("    Посмотреть что изменилось:", verbose)
                _log("      git log -p {}".format(m["filepath"]), verbose)
                _log("    Если изменение намеренное:", verbose)
                _log("      python migrate.py update-checksum {}".format(m["version"]), verbose)
            _log("", verbose)

        pending = [m for m in all_new if m["version"] not in applied]

        if not pending:
            _log("Нет новых миграций для применения.", verbose)
            return

        _log("Найдено {} новых миграций для применения:".format(len(pending)), verbose)
        for m in pending:
            _log("  - {} — {}".format(m["version"], m["description"]), verbose)

        # Проверяем depends_on (учитываем pending, которые идут раньше по timestamp)
        known = set(applied)
        for m in pending:
            check_depends_on(m, known)
            known.add(m["version"])

        # Настраиваем Alembic-контекст (чтобы op.execute() работал в миграциях)
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        migration_ctx = MigrationContext.configure(connection=conn)

        with Operations.context(migration_ctx):
            for m in pending:
                start = time.time()

                _log("  Применяю: {} — {}...".format(m["version"], m["description"]), verbose)
                m["module"].upgrade()

                duration_ms = int((time.time() - start) * 1000)

                conn.execute(
                    text(
                        "INSERT INTO {}.{} (version, description, duration_ms, checksum) "
                        "VALUES (:ver, :desc, :dur, :checksum)".format(
                            TABLE_SCHEMA, SCHEMA_MIGRATIONS_TABLE
                        )
                    ),
                    {
                        "ver": m["version"],
                        "desc": m["description"],
                        "dur": duration_ms,
                        "checksum": m["checksum"],
                    }
                )

                applied.add(m["version"])
                _log("  OK {} ({}ms)".format(m["version"], duration_ms), verbose)

    _log("Готово: применено {} миграций.".format(len(pending)), verbose)
