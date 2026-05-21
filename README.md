# KIP Calendar Service

FastAPI-сервис для одного персонального ICS-календаря на каждого работника:

```text
GET /cal/{token}.ics
```

Внутри одной ссылки собираются смены, КИП, проверки знаний, медкомиссии и инструкторские поездки.

## Локальный запуск

```bash
python -m venv .venv
. .venv/Scripts/activate
pip install -e ".[dev]"
copy .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

Для PostgreSQL укажите `DATABASE_URL`. Для быстрой локальной проверки можно оставить SQLite из `.env.example`.

## Railway

1. Создайте сервис из репозитория.
2. Добавьте PostgreSQL plugin.
3. Укажите переменные:
   - `DATABASE_URL` - Railway подставит автоматически из PostgreSQL.
   - `SECRET_KEY` - длинная случайная строка.
   - `ADMIN_USERNAME` - логин администратора.
   - `ADMIN_PASSWORD` - пароль администратора.
   - `ADMIN_CALENDAR_TOKEN` - необязательный токен общей админской ICS-ссылки. Если пусто, сервис создаст стабильный токен из `SECRET_KEY`.
   - `BASE_URL` - публичный URL сервиса, например `https://example.up.railway.app`.
4. Railway использует `Dockerfile`, при старте выполняет `alembic upgrade head`.

## Основные маршруты

- `GET /health` - проверка живости.
- `GET /admin` - главная панель администратора.
- `GET /admin/login`, `POST /admin/login` - вход.
- `GET /admin/employees` - работники.
- `GET /admin/employees/{id}` - карточка работника.
- `POST /admin/employees/{id}/rotate-token` - перевыпустить персональную ссылку.
- `GET /admin/uploads` - загрузка Excel.
- `POST /admin/uploads/preview` - предпросмотр без записи событий в БД.
- `POST /admin/uploads/{id}/confirm` - подтверждение импорта.
- `GET /cal/{token}.ics` - персональный календарь работника.
- `GET /cal/admin/{token}.ics` - общий календарь администратора по всем активным работникам без рабочего графика.

## Структура

- `app/models.py` - таблицы БД.
- `app/services/kip.py` - правила назначения КИП.
- `app/services/ics.py` - генерация ICS.
- `app/parsers/` - отдельные Excel-парсеры.
- `app/routers/` - публичные и админские маршруты.
- `tests/` - проверки ключевых бизнес-правил.
