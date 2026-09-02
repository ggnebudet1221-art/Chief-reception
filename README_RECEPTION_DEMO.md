# Chief Reception Demo

Изолированный MVP Telegram-бота для записи клиента в салон красоты или массажный кабинет.

Проект не использует основной `src`, основной `.env`, `ai_manager.db`, память, профиль, задачи, чекины или историю пользователя. Данные демо хранятся только в отдельной SQLite базе из `RECEPTION_DATABASE_PATH`.

## Что умеет MVP

- показывает список услуг и стоимость;
- показывает демонстрационные свободные слоты;
- ведет естественный диалог администратора;
- собирает имя, телефон, услугу, дату и время;
- создает заявку в отдельной SQLite базе;
- отправляет владельцу салона уведомление в Telegram.
- использует Anthropic-compatible endpoint как AI-администратора, если задан `ANTHROPIC_API_KEY`;
- автоматически переходит в локальный fallback-режим, если Claude API недоступен.

## Быстрый запуск

1. Создайте отдельного Telegram-бота через BotFather.
2. Скопируйте демо-конфиг:

```powershell
Copy-Item .env.demo.example .env.demo
```

3. Заполните `.env.demo`:

```dotenv
RECEPTION_TELEGRAM_BOT_TOKEN="отдельный_токен_демо_бота"
RECEPTION_OWNER_TELEGRAM_ID="telegram_id_владельца"
RECEPTION_DATABASE_PATH="demo_data/reception_demo.sqlite3"
RECEPTION_SALON_NAME="Beauty Room"
RECEPTION_TIMEZONE="Europe/Moscow"
ANTHROPIC_API_KEY="ключ_провайдера"
ANTHROPIC_BASE_URL="https://api.anthropic.com"
ANTHROPIC_MODEL="claude-sonnet-4-5"
CLAUDE_MAX_TOKENS="300"
```

Поддерживаются `UTC` и `Europe/Moscow` без системной timezone-базы. Если указать невалидную timezone, бот запишет warning в лог и продолжит работу в UTC.

## Логи

В процессе работы смотрите теги:

- `[AI]` - provider, модель, запросы к Anthropic, extraction и fallback;
- `[BOOKING]` - создание записи в SQLite;
- `[NOTIFICATION]` - отправка уведомления владельцу.

4. Запустите:

```powershell
$env:PYTHONPATH="C:\Users\Public\AIManagerVenv\Lib\site-packages"
& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" .\run_reception_bot.py
```

Если запускаете в окружении без проблемы кириллического пути Windows, можно использовать любой Python с пакетами `aiogram` и `python-dotenv`.

## Rahat Float Userbot

Для рабочего аккаунта администратора Telegram добавлен отдельный Telethon-runner:

```powershell
& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" .\run_rahat_userbot.py
```

Он читает настройки из `.env`:

```dotenv
TELEGRAM_API_ID="123456"
TELEGRAM_API_HASH="your_telegram_api_hash"
API_ID="123456"
API_HASH="your_telegram_api_hash"
TELEGRAM_SESSION_NAME="demo_data/rahat_float_admin"
ANTHROPIC_API_KEY="your_anthropic_api_key"
ANTHROPIC_BASE_URL="https://api.anthropic.com"
ANTHROPIC_MODEL="claude-sonnet-4-5"
CLAUDE_MAX_TOKENS="600"
USERBOT_MAX_HISTORY_MESSAGES="8"
RAHAT_LEAD_LOG_PATH="demo_data/rahat_userbot_leads.jsonl"
```

Userbot обрабатывает только входящие личные сообщения, имитирует набор текста и хранит последние 6-8 реплик по каждому клиенту. Когда Claude добавляет служебный блок `[BOOKING_LEAD: ...]`, скрипт вырезает его из ответа клиенту, один раз отправляет заявку в «Избранное» аккаунта администратора и сохраняет запись в `demo_data/rahat_userbot_leads.jsonl`.

## Команды бота

- `/start` - начать диалог;
- `/services` - список услуг;
- `/prices` - цены;
- `/times` - свободное время;
- `/book` - оформить запись;
- `/cancel` - отменить текущую запись.

Клиент также может писать естественно: `Здравствуйте, хочу массаж завтра`, `Можно уход за лицом 17.06 в 15:00`, `Меня зовут Анна`.

## Архитектура

- `chief_reception_demo/core/config.py` - отдельный конфиг из `.env.demo`;
- `chief_reception_demo/database/sqlite.py` - отдельная SQLite схема;
- `chief_reception_demo/database/repositories.py` - доступ к услугам и заявкам;
- `chief_reception_demo/services/catalog.py` - демо-каталог услуг;
- `chief_reception_demo/services/availability_service.py` - демо-слоты, дата, время и безопасная timezone;
- `chief_reception_demo/services/booking_service.py` - создание записи;
- `chief_reception_demo/services/notification_service.py` - уведомление владельцу;
- `chief_reception_demo/services/claude_client.py` - клиент Anthropic-compatible API;
- `chief_reception_demo/services/extraction_service.py` - extraction и нормализация данных записи;
- `chief_reception_demo/services/receptionist_service.py` - AI receptionist и fallback-режим;
- `chief_reception_demo/bot/dialog.py` - тонкий Telegram-адаптер.

Позже CRM или Google Calendar можно подключить заменой сервисов каталога и доступности, не переписывая Telegram-диалог. LLM-интеграция должна подключаться внутри `ReceptionistService`.
