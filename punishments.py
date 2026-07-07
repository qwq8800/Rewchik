"""
Punishment Engine — оркестратор наказаний (раздел 5 ТЗ, упрощённая версия для одного чата).

Получает вердикт от анти-модуля и применяет наказание согласно лестнице:
1 нарушение -> предупреждение (варн)
2+ нарушение -> мут по нарастающей (punishment_ladder из настроек)
после исчерпания лестницы -> бан

Автоматическое снятие мута реализовано через asyncio.create_task + asyncio.sleep,
что для одного чата эквивалентно Scheduler-у из "большого" ТЗ (там это отдельный
сервис на очередях, здесь — таск в том же процессе).
"""
import asyncio
import time
import logging

from aiogram import Bot
from aiogram.types import ChatPermissions

import db
import config

logger = logging.getLogger(__name__)

MUTED_PERMISSIONS = ChatPermissions(
    can_send_messages=False,
    can_send_audios=False,
    can_send_documents=False,
    can_send_photos=False,
    can_send_videos=False,
    can_send_video_notes=False,
    can_send_voice_notes=False,
    can_send_polls=False,
    can_send_other_messages=False,
    can_add_web_page_previews=False,
)

UNMUTED_PERMISSIONS = ChatPermissions(
    can_send_messages=True,
    can_send_audios=True,
    can_send_documents=True,
    can_send_photos=True,
    can_send_videos=True,
    can_send_video_notes=True,
    can_send_voice_notes=True,
    can_send_polls=True,
    can_send_other_messages=True,
    can_add_web_page_previews=True,
)


async def apply_verdict(bot: Bot, chat_id: int, user_id: int, module: str, reason: str, moderator_id: int = None):
    """severity уже не нужен здесь — решение принимается по накопленным варнам пользователя."""
    warns = await db.add_warning(user_id, f"[{module}] {reason}", moderator_id or 0)
    await db.add_log("verdict", user_id, moderator_id, f"module={module} reason={reason} warns={warns}")

    if warns == 1:
        # Первое нарушение — просто предупреждение
        await db.add_log("warn", user_id, moderator_id, reason)
        return "warn", None

    # Второе и последующие — мут по лестнице, либо бан, если лестница исчерпана
    ladder = await db.get_punishment_ladder()
    step_index = warns - 2  # warns=2 -> ladder[0], warns=3 -> ladder[1], ...
    if step_index < len(ladder):
        duration = ladder[step_index]
        await mute_user(bot, chat_id, user_id, duration, reason)
        return "mute", duration
    else:
        await ban_user(bot, chat_id, user_id, reason)
        return "ban", None


async def mute_user(bot: Bot, chat_id: int, user_id: int, duration_sec: int, reason: str = ""):
    until = int(time.time()) + duration_sec
    try:
        await bot.restrict_chat_member(
            chat_id=chat_id, user_id=user_id, permissions=MUTED_PERMISSIONS, until_date=until
        )
    except Exception as e:
        logger.warning(f"Не удалось замутить {user_id}: {e}")
        return
    await db.set_muted(user_id, until)
    await db.add_log("mute", user_id, None, f"duration={duration_sec}s reason={reason}")
    asyncio.create_task(_auto_unmute(bot, chat_id, user_id, duration_sec))


async def _auto_unmute(bot: Bot, chat_id: int, user_id: int, duration_sec: int):
    await asyncio.sleep(duration_sec)
    member = await db.get_member(user_id)
    if member and member["is_muted"] and member["muted_until"] <= int(time.time()) + 1:
        await unmute_user(bot, chat_id, user_id, automatic=True)


async def unmute_user(bot: Bot, chat_id: int, user_id: int, automatic: bool = False):
    try:
        await bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=UNMUTED_PERMISSIONS)
    except Exception as e:
        logger.warning(f"Не удалось снять мут с {user_id}: {e}")
    await db.clear_muted(user_id)
    await db.add_log("unmute" if not automatic else "auto_unmute", user_id, None, "")


async def ban_user(bot: Bot, chat_id: int, user_id: int, reason: str = "", moderator_id: int = None):
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
    except Exception as e:
        logger.warning(f"Не удалось забанить {user_id}: {e}")
        return
    await db.set_banned(user_id, True)
    await db.add_log("ban", user_id, moderator_id, reason)


async def unban_user(bot: Bot, chat_id: int, user_id: int, moderator_id: int = None):
    try:
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
    except Exception as e:
        logger.warning(f"Не удалось разбанить {user_id}: {e}")
        return
    await db.set_banned(user_id, False)
    await db.add_log("unban", user_id, moderator_id, "")


async def kick_user(bot: Bot, chat_id: int, user_id: int, reason: str = "", moderator_id: int = None):
    """Кик = бан + мгновенный разбан (пользователь может зайти обратно по ссылке)."""
    try:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
    except Exception as e:
        logger.warning(f"Не удалось кикнуть {user_id}: {e}")
        return
    await db.add_log("kick", user_id, moderator_id, reason)
