"""
Логика завершения розыгрыша, используемая и из main.py (автозавершение по таймеру,
команды /giveaway, /endgiveaway), и из panel.py (кнопка «Завершить» в админ-панели).
Вынесена в отдельный модуль, чтобы избежать циклического импорта — main.py уже
импортирует panel.py, поэтому panel.py не может импортировать main.py напрямую.
"""
import html
import logging

from aiogram import Bot

import db

logger = logging.getLogger(__name__)


async def _display_mention(bot: Bot, chat_id: int, user_id: int) -> str:
    """Упрощённая версия main.display_mention для контекста, где нет живого User-объекта —
    только user_id (например, победитель розыгрыша, определяемый уже после события)."""
    nickname = await db.get_nickname(user_id)
    if nickname:
        return f'<a href="tg://user?id={user_id}">{html.escape(nickname)}</a>'
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.user.mention_html()
    except Exception:
        return f'<a href="tg://user?id={user_id}">Победитель</a>'


async def finish_giveaway(bot: Bot, giveaway_id: int):
    """Завершает розыгрыш: выбирает случайного победителя (если есть участники) и объявляет
    результат. Общая логика для автозавершения по таймеру, /endgiveaway и кнопки в панели."""
    giveaway = await db.get_giveaway(giveaway_id)
    if giveaway is None or giveaway["status"] != "active":
        return False

    winner_id = await db.pick_random_giveaway_winner(giveaway_id)
    await db.finish_giveaway(giveaway_id, winner_id)
    await db.add_log("giveaway_finished", winner_id or 0, None, f"giveaway_id={giveaway_id}")

    if winner_id is None:
        text = f"🎉 <b>Розыгрыш завершён</b>\nПриз: {giveaway['prize']}\n\nК сожалению, никто не участвовал."
    else:
        winner_mention = await _display_mention(bot, giveaway["chat_id"], winner_id)
        text = f"🎉 <b>Розыгрыш завершён!</b>\nПриз: {giveaway['prize']}\n\nПобедитель: {winner_mention}! Поздравляем 🎊"

    try:
        if giveaway["message_id"]:
            await bot.edit_message_text(text, chat_id=giveaway["chat_id"], message_id=giveaway["message_id"])
        else:
            await bot.send_message(giveaway["chat_id"], text)
    except Exception:
        try:
            await bot.send_message(giveaway["chat_id"], text)
        except Exception as e:
            logger.warning(f"Не удалось объявить результат розыгрыша: {e}")

    return True
