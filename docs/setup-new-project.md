# Внедрение на проект с нуля

Пошаговая инструкция для проекта, где ещё нет миграций.

## Шаг 1: Структура проекта

Создайте структуру:

```
my_project/
├── migrate.py               ← точка входа CLI
├── migration_runner.py       ← runner
├── migration_generator.py    ← генератор файлов
└── migrations/               ← папка для миграций
```

```bash
mkdir migrations
```

## Шаг 2: Настройте `migrate.py`

Откройте `migrate.py` и адаптируйте секцию **КОНФИГУРАЦИЯ**:

```python
# URI подключения к БД
DATABASE_URI = os.environ.get("DATABASE_URI", "postgresql://user:pass@localhost:5432/mydb")

# Роль БД (SET ROLE). None — не устанавливать.
DATABASE_ROLE = None

# Схема для SET search_path. None — не устанавливать.
SEARCH_PATH = None

# Папки миграций (legacy не нужна для нового проекта)
LEGACY_DIR = None
NEW_DIR = os.path.join(APP_DIR, "migrations")
```

## Шаг 3: Настройте `migration_runner.py`

Если вы используете **PostgreSQL** (не Greenplum), измените:

```python
DISTRIBUTED_BY = ""  # для обычного PostgreSQL
# DISTRIBUTED_BY = "DISTRIBUTED BY (version)"  # для Greenplum
```

## Шаг 4: Создайте первую миграцию

```bash
export DATABASE_URI="postgresql://user:pass@localhost:5432/mydb"
python migrate.py create "create users table"
```

Отредактируйте созданный файл:

```python
def upgrade():
    op.execute("""
        CREATE TABLE users (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(200)
        )
    """)

def downgrade():
    op.execute("DROP TABLE IF EXISTS users")
```

## Шаг 5: Примените миграцию

```bash
python migrate.py upgrade -v
```

Вывод:
```
Проверяю таблицу schema_migrations...
Applied миграций в БД: 0
Найдено 1 новых миграций для применения:
  - 20260410_143022_a3f1 — create users table
  Применяю: 20260410_143022_a3f1 — create users table...
  OK 20260410_143022_a3f1 (45ms)
Готово: применено 1 миграций.
```

## Шаг 6: Проверьте статус

```bash
python migrate.py status
```

```
Всего в schema_migrations: 1
Новых миграций (migrations): 1
Pending: 0

Все миграции применены.
```

## Шаг 7: Интеграция с CI/CD

В вашем скрипте деплоя:

```python
from sqlalchemy import create_engine, pool
from migration_runner import run_upgrade

engine = create_engine(DATABASE_URI, poolclass=pool.NullPool)

run_upgrade(
    engine=engine,
    new_dir="migrations",
    verbose=True,
)
```

Или просто:

```bash
python migrate.py upgrade -v
```

## Готово

Теперь разработчики создают миграции через `python migrate.py create "описание"`,
а при деплое запускается `python migrate.py upgrade`. Конфликтов `multiple heads` нет.
