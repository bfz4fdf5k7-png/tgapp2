import asyncio
import os
import aiosqlite

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import Message
from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest, TelegramRetryAfter

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "orderspayx_users.db")

if not BOT_TOKEN or not ADMIN_ID:
    raise RuntimeError("Укажите переменные окружения BOT_TOKEN и ADMIN_ID")

router = Router()
# Ожидаемые сообщения админа: {admin_id: (chat_id, message_id)}
pending_content = {}


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                active INTEGER NOT NULL DEFAULT 1,
                joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.commit()


async def save_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO users(user_id, active) VALUES(?, 1)
            ON CONFLICT(user_id) DO UPDATE SET active=1
        """, (user_id,))
        await db.commit()


async def deactivate_user(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET active=0 WHERE user_id=?", (user_id,))
        await db.commit()


async def get_users():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT user_id FROM users WHERE active=1")
        return [row[0] for row in await cur.fetchall()]


@router.message(Command("start"))
async def start(message: Message):
    await save_user(message.from_user.id)
    await message.answer(
        "Привет! Ты подписан на уведомления OrdersPayX.\\n"
        "Чтобы отключить рассылку, отправь /stop."
    )


@router.message(Command("stop"))
async def stop(message: Message):
    await deactivate_user(message.from_user.id)
    await message.answer("Рассылка отключена. Чтобы снова подписаться, отправь /start.")


@router.message(Command("broadcast"))
async def broadcast_start(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    pending_content.pop(ADMIN_ID, None)
    await message.answer(
        "Отправь следующим сообщением текст, фото, видео или документ для рассылки.\\n"
        "Для отмены отправь /cancel."
    )
    pending_content[ADMIN_ID] = None


@router.message(Command("cancel"))
async def cancel(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    if ADMIN_ID in pending_content:
        pending_content.pop(ADMIN_ID, None)
        await message.answer("Рассылка отменена.")
    else:
        await message.answer("Сейчас нет подготовленной рассылки.")


@router.message(Command("send"))
async def send_broadcast(message: Message, bot: Bot):
    if message.from_user.id != ADMIN_ID:
        return
    item = pending_content.get(ADMIN_ID)
    if not item:
        await message.answer("Сначала отправь /broadcast, затем сообщение для рассылки.")
        return

    source_chat_id, source_message_id = item
    users = await get_users()
    if not users:
        pending_content.pop(ADMIN_ID, None)
        await message.answer("Пока нет подписчиков, которые включили рассылку.")
        return

    await message.answer(f"Начинаю рассылку для {len(users)} подписчиков…")
    sent = failed = 0
    blocked = []

    for user_id in users:
        try:
            await bot.copy_message(
                chat_id=user_id,
                from_chat_id=source_chat_id,
                message_id=source_message_id
            )
            sent += 1
            await asyncio.sleep(0.06)
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after + 0.5)
            try:
                await bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=source_chat_id,
                    message_id=source_message_id
                )
                sent += 1
            except Exception:
                failed += 1
        except TelegramForbiddenError:
            blocked.append(user_id)
            failed += 1
        except TelegramBadRequest:
            failed += 1
        except Exception:
            failed += 1

    if blocked:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.executemany(
                "UPDATE users SET active=0 WHERE user_id=?",
                [(uid,) for uid in blocked]
            )
            await db.commit()

    pending_content.pop(ADMIN_ID, None)
    await message.answer(
        "Рассылка завершена.\\n"
        f"Успешно: {sent}\\n"
        f"Не отправлено: {failed}\\n"
        f"Всего подписчиков: {len(users)}"
    )


@router.message()
async def collect_broadcast_content(message: Message):
    if message.from_user.id != ADMIN_ID or ADMIN_ID not in pending_content:
        return
    if message.text and message.text.startswith("/"):
        return

    pending_content[ADMIN_ID] = (message.chat.id, message.message_id)
    await message.answer(
        "Сообщение подготовлено. Проверь его выше.\\n"
        "Отправить всем подписчикам: /send\\n"
        "Отменить: /cancel"
    )


async def main():
    await init_db()
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
