"""
FL.ru RSS → Telegram
Бот следит за RSS-лентой заказов FL.ru и присылает новые заказы
(с фильтром по ключевым словам) владельцу в Telegram.
"""
import asyncio
import html
import logging
import os
import re
import sqlite3
from datetime import datetime

import aiohttp
import feedparser
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import LinkPreviewOptions, Message
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
RSS_URLS = os.getenv("RSS_URLS", "").split()  # несколько ссылок — через пробел
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "120"))  # секунды
DB_PATH = os.getenv("DB_PATH", "fl_bot.db")

HEADERS = {"User-Agent": "Mozilla/5.0 (FL-RSS-Telegram-bot)"}

# ---------- база данных ----------

db = sqlite3.connect(DB_PATH)
db.executescript("""
CREATE TABLE IF NOT EXISTS seen     (id TEXT PRIMARY KEY, added_at TEXT);
CREATE TABLE IF NOT EXISTS keywords (word TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
""")
db.commit()


def is_seen(eid: str) -> bool:
    return db.execute("SELECT 1 FROM seen WHERE id = ?", (eid,)).fetchone() is not None


def mark_seen(eid: str) -> None:
    db.execute("INSERT OR IGNORE INTO seen VALUES (?, ?)", (eid, datetime.now().isoformat()))
    db.commit()


def get_keywords() -> list[str]:
    return [r[0] for r in db.execute("SELECT word FROM keywords ORDER BY word")]


def add_keyword(word: str) -> None:
    db.execute("INSERT OR IGNORE INTO keywords VALUES (?)", (word,))
    db.commit()


def del_keyword(word: str) -> bool:
    cur = db.execute("DELETE FROM keywords WHERE word = ?", (word,))
    db.commit()
    return cur.rowcount > 0


def get_setting(key: str, default: str = "") -> str:
    row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(key: str, value: str) -> None:
    db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))
    db.commit()


# ---------- работа с RSS ----------

def clean(text: str, limit: int = 400) -> str:
    """Убирает HTML-теги и лишние пробелы, обрезает до limit символов."""
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def matches(entry, keywords: list[str]) -> bool:
    if not keywords:
        return True  # нет фильтра — присылаем всё
    text = f"{entry.get('title', '')} {entry.get('summary', '')}".lower()
    return any(k in text for k in keywords)


def format_entry(entry) -> str:
    title = html.escape(clean(entry.get("title", ""), 200))
    desc = html.escape(clean(entry.get("summary", "")))
    link = html.escape(entry.get("link", ""))
    return f"🆕 <b>{title}</b>\n\n{desc}\n\n<a href=\"{link}\">Открыть заказ →</a>"


async def fetch_entries(session: aiohttp.ClientSession, url: str):
    timeout = aiohttp.ClientTimeout(total=20)
    async with session.get(url, headers=HEADERS, timeout=timeout) as resp:
        resp.raise_for_status()
        data = await resp.read()
    return feedparser.parse(data).entries


async def check_once(bot: Bot, session: aiohttp.ClientSession, notify: bool = True) -> int:
    """Проверяет все ленты. Возвращает число отправленных заказов."""
    keywords = get_keywords()
    sent = 0
    for url in RSS_URLS:
        try:
            entries = await fetch_entries(session, url)
        except Exception as ex:
            logging.warning("Не удалось загрузить %s: %s", url, ex)
            continue
        for entry in reversed(entries):  # старые первыми, чтобы порядок был хронологический
            eid = entry.get("id") or entry.get("link")
            if not eid or is_seen(eid):
                continue
            mark_seen(eid)
            if notify and matches(entry, keywords):
                try:
                    await bot.send_message(
                        ADMIN_ID,
                        format_entry(entry),
                        link_preview_options=LinkPreviewOptions(is_disabled=True),
                    )
                    sent += 1
                    await asyncio.sleep(0.5)  # не упираемся в лимиты Telegram
                except Exception as ex:
                    logging.warning("Не удалось отправить сообщение: %s", ex)
    return sent


async def watcher(bot: Bot) -> None:
    async with aiohttp.ClientSession() as session:
        # При самом первом запуске просто запоминаем текущие заказы,
        # чтобы не завалить чат старыми.
        if get_setting("initialized") != "1":
            await check_once(bot, session, notify=False)
            set_setting("initialized", "1")
            logging.info("Первый запуск: текущие заказы помечены как просмотренные")

        while True:
            if get_setting("paused") != "1":
                try:
                    n = await check_once(bot, session)
                    if n:
                        logging.info("Отправлено новых заказов: %s", n)
                except Exception:
                    logging.exception("Ошибка при проверке лент")
            await asyncio.sleep(CHECK_INTERVAL)


# ---------- команды бота ----------

dp = Dispatcher()
dp.message.filter(F.from_user.id == ADMIN_ID)  # бот отвечает только владельцу

HELP = (
    "Я слежу за заказами на FL.ru и присылаю новые.\n\n"
    "/status — состояние\n"
    "/kw — список ключевых слов\n"
    "/addkw бот, парсер — добавить слова (через запятую)\n"
    "/delkw бот — удалить слово\n"
    "/check — проверить прямо сейчас\n"
    "/pause и /resume — выключить / включить уведомления\n\n"
    "Если ключевых слов нет — присылаю все заказы из ленты."
)


@dp.message(Command("start", "help"))
async def cmd_start(message: Message):
    await message.answer(HELP)


@dp.message(Command("status"))
async def cmd_status(message: Message):
    paused = get_setting("paused") == "1"
    kw = get_keywords()
    await message.answer(
        f"Состояние: {'⏸ на паузе' if paused else '▶️ работаю'}\n"
        f"Лент: {len(RSS_URLS)}\n"
        f"Проверка каждые {CHECK_INTERVAL} сек.\n"
        f"Ключевые слова: {', '.join(kw) if kw else 'нет (присылаю всё)'}"
    )


@dp.message(Command("kw"))
async def cmd_kw(message: Message):
    kw = get_keywords()
    await message.answer("Ключевые слова: " + (", ".join(kw) if kw else "нет"))


@dp.message(Command("addkw"))
async def cmd_addkw(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Пример: /addkw телеграм, бот, парсер")
        return
    words = [w.strip().lower() for w in command.args.split(",") if w.strip()]
    for w in words:
        add_keyword(w)
    await message.answer("Добавил: " + ", ".join(words))


@dp.message(Command("delkw"))
async def cmd_delkw(message: Message, command: CommandObject):
    word = (command.args or "").strip().lower()
    if not word:
        await message.answer("Пример: /delkw парсер")
        return
    await message.answer("Удалил." if del_keyword(word) else "Такого слова нет.")


@dp.message(Command("check"))
async def cmd_check(message: Message, bot: Bot):
    async with aiohttp.ClientSession() as session:
        n = await check_once(bot, session)
    await message.answer(f"Готово. Новых заказов: {n}")


@dp.message(Command("pause"))
async def cmd_pause(message: Message):
    set_setting("paused", "1")
    await message.answer("⏸ Уведомления на паузе. /resume — включить.")


@dp.message(Command("resume"))
async def cmd_resume(message: Message):
    set_setting("paused", "0")
    await message.answer("▶️ Снова слежу за заказами.")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not BOT_TOKEN or not ADMIN_ID or not RSS_URLS:
        raise SystemExit("Заполни BOT_TOKEN, ADMIN_ID и RSS_URLS в файле .env")

    bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    watcher_task = asyncio.create_task(watcher(bot))
    try:
        await dp.start_polling(bot)
    finally:
        watcher_task.cancel()


if __name__ == "__main__":
    asyncio.run(main())
