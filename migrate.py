"""
CLI для управления бесконфликтными миграциями.

Использование:
    python migrate.py create "описание миграции"
    python migrate.py create "описание" --depends-on 20260410_143022_a3f1
    python migrate.py upgrade -v
    python migrate.py status
    python migrate.py rollback <version>
    python migrate.py update-checksum <version>

Адаптируйте секцию КОНФИГУРАЦИЯ под свой проект.
"""
import argparse
import os
import sys
import time

from sqlalchemy import create_engine, pool, text


# ──────────────────────────────────────────────
# КОНФИГУРАЦИЯ — адаптируйте под свой проект
# ──────────────────────────────────────────────

# URI подключения к БД
DATABASE_URI = os.environ.get("DATABASE_URI", "postgresql://user:pass@localhost:5432/mydb")

# Роль БД (SET ROLE). None — не устанавливать.
DATABASE_ROLE = os.environ.get("DATABASE_ROLE", None)

# Схема для SET search_path. None — не устанавливать.
SEARCH_PATH = os.environ.get("SEARCH_PATH", None)

# Папки миграций
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LEGACY_DIR = os.path.join(APP_DIR, "alembic_migrations", "versions")    # старые миграции
NEW_DIR = os.path.join(APP_DIR, "alembic_migrations", "versions_v2")    # новые миграции

# ──────────────────────────────────────────────


def get_engine():
    return create_engine(
        DATABASE_URI,
        poolclass=pool.NullPool,
        connect_args={"application_name": "migration_cli"},
    )


# ──────────────────────────────────────────────
# Команды
# ──────────────────────────────────────────────

def cmd_upgrade(args):
    """Применить все pending-миграции."""
    from migration_runner import run_upgrade

    engine = get_engine()
    legacy = LEGACY_DIR if os.path.isdir(LEGACY_DIR) else None

    run_upgrade(
        engine=engine,
        new_dir=NEW_DIR,
        legacy_dir=legacy,
        db_role=DATABASE_ROLE,
        search_path=SEARCH_PATH,
        verbose=args.verbose,
    )


def cmd_status(args):
    """Показать статус миграций."""
    from migration_runner import (
        ensure_table, get_applied_versions, scan_migration_files, check_checksums,
    )

    engine = get_engine()

    with engine.begin() as conn:
        if DATABASE_ROLE:
            conn.execute(text("SET ROLE {}".format(DATABASE_ROLE)))
        ensure_table(conn)
        applied = get_applied_versions(conn)

    all_new = scan_migration_files(NEW_DIR)
    pending = [m for m in all_new if m["version"] not in applied]

    print("Всего в schema_migrations: {}".format(len(applied)))
    print("Новых миграций (versions_v2): {}".format(len(all_new)))
    print("Pending: {}".format(len(pending)))

    if pending:
        print("\nОжидают применения:")
        for m in pending:
            print("  - {} — {}".format(m["version"], m["description"]))

    if all_new and not pending:
        print("\nВсе миграции применены.")

    # Проверка checksum
    with engine.begin() as conn:
        if DATABASE_ROLE:
            conn.execute(text("SET ROLE {}".format(DATABASE_ROLE)))
        tampered = check_checksums(conn, all_new)

    if tampered:
        print("")
        print("WARNING: обнаружены изменения в уже applied миграциях:")
        for m in tampered:
            print("  - {} ({})".format(m["version"], m["description"]))
            print("    Посмотреть что изменилось:")
            print("      git log -p {}".format(m["filepath"]))
            print("    Если изменение намеренное:")
            print("      python migrate.py update-checksum {}".format(m["version"]))


def cmd_create(args):
    """Создать новую миграцию."""
    from migration_generator import create_migration

    create_migration(
        description=args.message,
        versions_dir=NEW_DIR,
        depends_on=args.depends_on,
    )


def cmd_rollback(args):
    """Откатить конкретную миграцию (для dev/staging)."""
    from migration_runner import (
        ensure_table,
        get_applied_versions,
        load_migration_module,
        scan_migration_files,
        SCHEMA_MIGRATIONS_TABLE,
        TABLE_SCHEMA,
    )

    version = args.version

    # Найти файл миграции по version
    filepath = None
    if os.path.isdir(NEW_DIR):
        for filename in os.listdir(NEW_DIR):
            if not filename.endswith(".py"):
                continue
            candidate = os.path.join(NEW_DIR, filename)
            module = load_migration_module(candidate)
            if module.revision == version:
                filepath = candidate
                break

    if not filepath:
        print("Файл миграции с version={} не найден в {}".format(version, NEW_DIR))
        sys.exit(1)

    module = load_migration_module(filepath)

    if not hasattr(module, "downgrade"):
        print("Миграция {} не содержит функцию downgrade()".format(version))
        sys.exit(1)

    engine = get_engine()

    # Проверяем что миграция применена
    with engine.begin() as conn:
        if DATABASE_ROLE:
            conn.execute(text("SET ROLE {}".format(DATABASE_ROLE)))
        ensure_table(conn)
        applied = get_applied_versions(conn)

    if version not in applied:
        print("Миграция {} не применена — откатывать нечего".format(version))
        sys.exit(1)

    # Проверяем: нет ли applied-миграций, которые зависят от откатываемой
    all_new = scan_migration_files(NEW_DIR)
    dependents = []
    for m in all_new:
        if m["version"] not in applied:
            continue
        deps = m["depends_on"]
        if not deps:
            continue
        if isinstance(deps, str):
            deps = [deps]
        if version in deps:
            dependents.append(m["version"])

    if dependents:
        print("Нельзя откатить {} — от неё зависят applied-миграции:".format(version))
        for d in dependents:
            print("  - {}".format(d))
        print("Сначала откатите зависимые миграции.")
        sys.exit(1)

    # Откатываем в транзакции
    with engine.begin() as conn:
        if DATABASE_ROLE:
            conn.execute(text("SET ROLE {}".format(DATABASE_ROLE)))
        if SEARCH_PATH:
            conn.execute(text("SET search_path TO {}".format(SEARCH_PATH)))

        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        migration_ctx = MigrationContext.configure(connection=conn)

        print("Откатываю: {} — {}".format(version, getattr(module, "description", "")))

        start = time.time()
        with Operations.context(migration_ctx):
            module.downgrade()

        conn.execute(
            text("DELETE FROM {}.{} WHERE version = :ver".format(
                TABLE_SCHEMA, SCHEMA_MIGRATIONS_TABLE
            )),
            {"ver": version},
        )

        duration_ms = int((time.time() - start) * 1000)

    print("  Откачено: {} ({}ms)".format(version, duration_ms))


def cmd_update_checksum(args):
    """Обновить checksum миграции после намеренного изменения файла."""
    from migration_runner import (
        ensure_table,
        get_applied_versions,
        load_migration_module,
        update_checksum,
        file_checksum,
    )

    version = args.version

    filepath = None
    if os.path.isdir(NEW_DIR):
        for filename in os.listdir(NEW_DIR):
            if not filename.endswith(".py"):
                continue
            candidate = os.path.join(NEW_DIR, filename)
            module = load_migration_module(candidate)
            if module.revision == version:
                filepath = candidate
                break

    if not filepath:
        print("Файл миграции с version={} не найден в {}".format(version, NEW_DIR))
        sys.exit(1)

    engine = get_engine()

    with engine.begin() as conn:
        if DATABASE_ROLE:
            conn.execute(text("SET ROLE {}".format(DATABASE_ROLE)))
        ensure_table(conn)
        applied = get_applied_versions(conn)

        if version not in applied:
            print("Миграция {} не найдена в schema_migrations".format(version))
            sys.exit(1)

        new_checksum = file_checksum(filepath)
        update_checksum(conn, version, new_checksum)

    print("Checksum обновлён: {} -> {}".format(version, new_checksum))


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="CLI для бесконфликтных миграций (alembic-no-conflict)"
    )
    sub = parser.add_subparsers(dest="command")

    p_upgrade = sub.add_parser("upgrade", help="Применить все pending-миграции")
    p_upgrade.add_argument("-v", "--verbose", action="store_true", help="Подробный вывод")

    sub.add_parser("status", help="Показать статус миграций (applied / pending)")

    p_create = sub.add_parser("create", help="Создать новую миграцию")
    p_create.add_argument("message", help="Описание миграции")
    p_create.add_argument(
        "--depends-on",
        default=None,
        help="Version миграции-зависимости (опционально)",
    )

    p_rollback = sub.add_parser("rollback", help="Откатить конкретную миграцию")
    p_rollback.add_argument("version", help="Version миграции для отката")

    p_checksum = sub.add_parser("update-checksum", help="Обновить checksum после правки файла")
    p_checksum.add_argument("version", help="Version миграции")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    commands = {
        "upgrade": cmd_upgrade,
        "status": cmd_status,
        "create": cmd_create,
        "rollback": cmd_rollback,
        "update-checksum": cmd_update_checksum,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
