# alembic-no-conflict

Бесконфликтная система миграций для SQLAlchemy/Alembic проектов.

Решает главную проблему Alembic при работе в команде: **конфликты `multiple heads`**
при параллельной разработке. Каждый раз, когда два разработчика создают миграции от
одного head и мержат в main — Alembic ломается и требует `alembic merge heads`.

## Как это работает

Стандартный Alembic строит **цепочку** миграций через `down_revision`:
```
aaa → bbb → ccc → ddd (head)
```
Два разработчика создают миграции от `ccc` → два head-а → конфликт.

Наш подход: **убрать цепочку**. Каждая миграция независимая (`down_revision = None`),
порядок определяется timestamp в имени файла, а таблица `schema_migrations`
хранит **все** применённые версии (вместо одной строки в `alembic_version`).

```
Файлы на диске:                  Таблица schema_migrations:
  20260409_100000_a3f1.py          20260409_100000_a3f1  ✓ applied
  20260409_140000_b7c2.py          20260409_140000_b7c2  ✓ applied
  20260410_090000_c8d9.py          (нет записи)          → pending

pending = файлы - applied → применяем по порядку timestamp
```

Два разработчика создают миграции параллельно — это просто два разных файла
с разными именами. Git merge проходит без конфликтов. Runner при деплое
видит оба файла как pending и применяет оба.

## Возможности

- Параллельная разработка миграций **без конфликтов**
- Совместимость с `op.execute()`, `op.add_column()` и другими операциями Alembic
- Таблица `schema_migrations` — полная история применённых миграций с аудитом
- Опциональный `depends_on` для явных зависимостей между миграциями
- Защита от одновременного запуска (`LOCK TABLE`)
- Rollback конкретных миграций с проверкой зависимостей
- Проверка целостности файлов (checksum) — обнаружение изменений в applied-миграциях
- Автор миграции из `git config user.name`
- Поддержка PostgreSQL и Greenplum

## Быстрый старт

### Установка в проект

Скопируйте три файла в корень вашего проекта миграций (рядом с `alembic.ini`):

```
your_project/
├── alembic.ini
├── alembic_migrations/
│   ├── env.py
│   └── versions/           ← существующие миграции
├── migration_runner.py      ← скопировать
├── migration_generator.py   ← скопировать
└── migrate.py               ← скопировать
```

Создайте папку для новых миграций:

```bash
mkdir alembic_migrations/versions_v2
```

### Использование

```bash
python migrate.py create "add email to users"        # создать миграцию
python migrate.py create "add fk" --depends-on <ver>  # с зависимостью
python migrate.py upgrade -v                           # применить все pending
python migrate.py status                               # показать статус
python migrate.py rollback <version>                   # откатить миграцию
python migrate.py update-checksum <version>            # обновить checksum после правки файла
```

## Подробная документация

- [Внедрение на проект с нуля](docs/setup-new-project.md)
- [Внедрение на существующий проект](docs/setup-existing-project.md)
- [Архитектура и принцип работы](docs/architecture.md)
- [Сравнение подходов](docs/comparison.md)

## Требования

- Python 3.8+
- SQLAlchemy 1.4+
- Alembic (для `op.execute()` и других операций)
- PostgreSQL или Greenplum

## Источники и вдохновение

- [Making Alembic migrations more team-friendly (Alan, Medium)](https://medium.com/alan/making-alembic-migrations-more-team-friendly-e92997f60eb2)
- [Обсуждение в Alembic GitHub](https://github.com/sqlalchemy/alembic/discussions/1608)
- Подход Ruby on Rails (`schema_migrations` table)
