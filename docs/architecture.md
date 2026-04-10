# Архитектура и принцип работы

## Проблема

Alembic строит цепочку миграций через `down_revision`:

```
aaa → bbb → ccc (head)
```

Два разработчика создают миграции от одного head:

```
              ┌→ ddd   (down_revision = ccc)
aaa → bbb → ccc
              └→ eee   (down_revision = ccc)
```

Alembic видит два head-а и падает: `Multiple heads detected!`
Решение `alembic merge heads` нужно каждый раз при параллельной работе.

## Решение

Убрать цепочку. Каждая миграция независимая:

```python
revision = '20260409_100000_a3f1'
down_revision = None               # ← нет зависимости
```

Порядок — по timestamp в имени файла.

## Таблица `schema_migrations`

Вместо одной строки в `alembic_version` — все применённые миграции:

```
schema_migrations
┌──────────────────────┬──────────────────────┬─────────────────────┬─────────────┐
│ version (PK)         │ description          │ applied_at          │ duration_ms │
├──────────────────────┼──────────────────────┼─────────────────────┼─────────────┤
│ 20260401_090000_a3f1 │ create users table   │ 2026-04-01 09:15:00 │ 120         │
│ 20260409_100000_b7c2 │ add email to users   │ 2026-04-09 10:02:00 │ 45          │
└──────────────────────┴──────────────────────┴─────────────────────┴─────────────┘
```

## Алгоритм `run_upgrade`

```
┌──────────────────────────────────────────────────────────┐
│ 1. CREATE TABLE IF NOT EXISTS schema_migrations          │
│    (идемпотентно — безопасно вызывать каждый раз)        │
├──────────────────────────────────────────────────────────┤
│ 2. BASELINE (только если таблица пустая)                 │
│    Сканирует legacy versions/ (regex, без import)        │
│    INSERT всех revision как applied                      │
├──────────────────────────────────────────────────────────┤
│ 3. BEGIN + LOCK TABLE schema_migrations                  │
│    SET ROLE / SET search_path                            │
├──────────────────────────────────────────────────────────┤
│ 4. applied = SELECT version FROM schema_migrations       │
│    all_files = scan versions_v2/                         │
│    pending = all_files - applied                         │
│    sorted by timestamp in filename                       │
├──────────────────────────────────────────────────────────┤
│ 5. Проверка depends_on для каждой pending                │
│    known = applied + предыдущие pending                  │
│    Если зависимость не в known → RuntimeError            │
├──────────────────────────────────────────────────────────┤
│ 6. MigrationContext.configure(conn)                      │
│    Operations.context(ctx)                               │
│    → op.execute() и другие операции Alembic работают     │
├──────────────────────────────────────────────────────────┤
│ 7. Для каждой pending:                                   │
│    module.upgrade()                                      │
│    INSERT INTO schema_migrations (version, ...)          │
├──────────────────────────────────────────────────────────┤
│ 8. COMMIT                                                │
│    Все миграции + все записи = одна транзакция            │
│    При ошибке → ROLLBACK всего                           │
└──────────────────────────────────────────────────────────┘
```

## Транзакционная модель

Все миграции в одной транзакции с `LOCK TABLE`:
- Лок держится всё время → защита от параллельного запуска
- При ошибке откатывается всё → чистое состояние
- При повторном запуске → pending тот же (ничего не записалось)

Альтернативы (не реализованы, но возможны):
- **Подход B** (два соединения): лок на одном, миграции на другом, commit после каждой
- **Подход C** (лок на каждую): LOCK → migrate → COMMIT → LOCK → migrate → COMMIT
- **Подход D** (savepoints): один лок, SAVEPOINT между миграциями

## Генерация revision

```
timestamp (UTC)  +  случайный суффикс  =  revision
20260409_143022  +  a3f1               =  20260409_143022_a3f1

Имя файла: 20260409_143022_a3f1_add_email_to_users.py
```

Суффикс (4 hex символа = 65536 вариантов) предотвращает коллизию,
если два разработчика создают миграцию в одну секунду.

## depends_on

Опциональное поле для явных зависимостей:

```python
revision = '20260410_090000_c8d9'
depends_on = '20260409_100000_a3f1'   # не применять, пока a3f1 не applied
```

Runner проверяет: зависимость должна быть в `applied` или в pending с меньшим timestamp.
Rollback проверяет: нельзя откатить миграцию, если от неё зависят applied-миграции.

## Совместимость с Alembic `op.*`

Миграции продолжают использовать `from alembic import op`:

```python
def upgrade():
    op.execute("ALTER TABLE users ADD COLUMN email VARCHAR(100)")
    op.add_column("users", sa.Column("phone", sa.String(20)))
```

Это работает благодаря `MigrationContext.configure()` + `Operations.context()`,
которые настраивают глобальный контекст Alembic перед выполнением миграций.

## Проверка целостности (checksum)

При применении миграции runner считает SHA-256 файла и записывает в таблицу.
При каждом `upgrade` и `status` runner сравнивает текущий checksum файла с записанным.

```
Файл не менялся:   checksum файла == checksum в таблице  →  тихо
Файл изменился:    checksum файла != checksum в таблице  →  WARNING
```

Warning **не блокирует** применение pending-миграций. Это информационное сообщение.

Если изменение намеренное (поправили комментарий, отформатировали код):
```bash
python migrate.py update-checksum <version>
```

Для legacy-миграций (baseline) checksum = NULL — они не проверяются.

Содержимое старой версии файла в таблице **не хранится**. Чтобы увидеть diff,
используйте git:
```bash
git log -p path/to/migration_file.py
```
