# Внедрение на существующий проект с Alembic

Пошаговая инструкция для проекта, где уже есть Alembic и N миграций.

## Предварительные условия

- Проект использует Alembic с SQLAlchemy
- Есть папка `versions/` с существующими миграциями
- Есть таблица `alembic_version` в БД

## Шаг 1: Скопируйте файлы

Скопируйте три файла рядом с `alembic.ini`:

```
your_project/
├── alembic.ini                  ← уже есть
├── alembic_migrations/
│   ├── env.py                   ← уже есть
│   ├── versions/                ← уже есть (N миграций)
│   └── versions_v2/             ← создать (новая папка)
├── migration_runner.py          ← скопировать
├── migration_generator.py       ← скопировать
└── migrate.py                   ← скопировать
```

```bash
mkdir alembic_migrations/versions_v2
```

## Шаг 2: Настройте `migrate.py`

Адаптируйте секцию **КОНФИГУРАЦИЯ**:

```python
DATABASE_URI = os.environ.get("DATABASE_URI", "postgresql://...")
DATABASE_ROLE = "your_role"          # или None
SEARCH_PATH = "your_schema"          # или None

LEGACY_DIR = os.path.join(APP_DIR, "alembic_migrations", "versions")   # старые
NEW_DIR = os.path.join(APP_DIR, "alembic_migrations", "versions_v2")   # новые
```

## Шаг 3: Настройте `migration_runner.py`

PostgreSQL:
```python
DISTRIBUTED_BY = ""
```

Greenplum:
```python
DISTRIBUTED_BY = "DISTRIBUTED BY (version)"
```

## Шаг 4: Первый запуск (baseline)

```bash
python migrate.py upgrade -v
```

Вывод:
```
Проверяю таблицу schema_migrations...
таблица schema_migrations пуста — заполняю baseline из legacy-миграций...
Baseline: записано 523 legacy-миграций
Applied миграций в БД: 523
Нет новых миграций для применения.
```

Что произошло:
1. Создалась таблица `public.schema_migrations`
2. Все N legacy-миграций записались как applied (из папки `versions/`)
3. Папка `versions_v2/` пуста — нечего применять

**Важно**: таблица `alembic_version` не тронута. Она остаётся как есть.

## Шаг 5: Заблокируйте стандартный Alembic

Добавьте в конец `env.py` (перед вызовом `run_migrations_online()`):

```python
import sys

print("=" * 70)
print("ВНИМАНИЕ: стандартный Alembic больше не используется в этом проекте.")
print("Используйте новый CLI:")
print('  python migrate.py create "описание"   — создать миграцию')
print("  python migrate.py upgrade -v           — применить миграции")
print("  python migrate.py status               — показать статус")
print("  python migrate.py rollback <version>   — откатить миграцию")
print("=" * 70)
sys.exit(1)

# Legacy код — оставлен для справки, не выполняется
# run_migrations_online()
```

Теперь `alembic upgrade head` и другие команды покажут предупреждение и завершатся.

## Шаг 6: Адаптируйте скрипт деплоя

Если у вас есть скрипт деплоя, который вызывает `alembic_command.upgrade(config, "head")`,
замените на:

```python
from sqlalchemy import create_engine, pool
from migration_runner import run_upgrade

engine = create_engine(DATABASE_URI, poolclass=pool.NullPool)

run_upgrade(
    engine=engine,
    new_dir="alembic_migrations/versions_v2",
    legacy_dir="alembic_migrations/versions",
    db_role="your_role",
    search_path="your_schema",
    verbose=True,
)
```

## Шаг 7: Создайте первую новую миграцию

```bash
python migrate.py create "add email to users"
python migrate.py upgrade -v
```

## Шаг 8: Оповестите команду

Разошлите инструкцию:

```
С сегодняшнего дня миграции создаются через:
  python migrate.py create "описание"

Вместо:
  alembic revision --autogenerate -m "описание"

Для применения:
  python migrate.py upgrade -v

Вместо:
  alembic upgrade head
```

## Что происходит с legacy

| Компонент | Статус |
|---|---|
| `alembic_version` (таблица) | Остаётся в БД, не используется |
| `versions/` (папка) | Не трогаем, нужна для baseline |
| `env.py` | Заблокирован, показывает предупреждение |
| `alembic.ini` | Остаётся, не мешает |

В будущем, когда убедитесь в стабильности:
- `alembic_version` можно удалить
- `env.py` можно оставить как есть (заблокированный)

## Откат на стандартный Alembic

Если нужно вернуться:
1. Уберите блокировку из `env.py`
2. Верните `run_migrations_online()` в `env.py`
3. Верните `alembic_command.upgrade(config, "head")` в скрипт деплоя
4. Новые миграции из `versions_v2/` нужно будет перенести в `versions/`
   и добавить `down_revision` для встраивания в цепочку
