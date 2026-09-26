[README.md](https://github.com/user-attachments/files/32686413/README.md)
# FL.ru → Telegram

Бот присылает в Telegram новые заказы с FL.ru, как только они появляются в RSS-ленте.
Можно фильтровать по ключевым словам прямо из чата.

## Запуск

1. Создай бота у @BotFather, получи токен.
2. Узнай свой ID у @userinfobot.
3. На FL.ru выставь нужные категории и фильтры, нажми «Подписаться через RSS» и скопируй ссылку.
4. Скопируй `.env.example` в `.env` и заполни.
5. Установи зависимости и запусти:

```
pip install -r requirements.txt
python bot.py
```

6. Напиши своему боту /start (иначе Telegram не даст ему написать тебе первым).

При первом запуске бот запоминает уже существующие заказы и присылает только новые.
[bot.py](https://github.com/user-attachments/files/32686432/bot.py)

## Команды

- `/status` — состояние
- `/kw`, `/addkw слово, слово`, `/delkw слово` — ключевые слова
- `/check` — проверить сейчас
- `/pause`, `/resume` — выключить / включить уведомления

## Сте[requirements.txt](https://github.com/user-attachments/files/32686433/requirements.txt)
к

Python, aiogram 3, aiohttp, feedparser, SQLite.
