"""
Точка входа бота @RewchikChat.

Бот работает ИСКЛЮЧИТЕЛЬНО в чате config.ALLOWED_CHAT_ID.
Если бота добавляют в любой другой чат — он присылает сообщение и сразу выходит
(middleware RestrictToSingleChat ниже перехватывает это до любых других обработчиков).
"""
import asyncio
import logging

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message, ChatMemberUpdated
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter, JOIN_TRANSITION

import config
import db
import moderation
import punishments
import keyboards
from panel import router as panel_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("rewchik_bot")

router = Router(name="main")


# ---------------------------------------------------------------------------
# Ограничение бота одним чатом
# ---------------------------------------------------------------------------

@router.my_chat_member()
async def on_bot_added_to_chat(event: ChatMemberUpdated, bot: Bot):
    """Если бота куда-то добавили — проверяем chat_id и выходим, если это не наш чат."""
    if event.chat.id != config.ALLOWED_CHAT_ID:
        try:
            await bot.send_message(
                event.chat.id,
                "⚠️ Этот бот настроен только для чата @RewchikChat и не может работать в других чатах.",
            )
        except Exception:
            pass
        try:
            await bot.leave_chat(event.chat.id)
        except Exception as e:
            logger.warning(f"Не удалось выйти из чужого чата {event.chat.id}: {e}")


def _is_allowed_chat(chat_id: int) -> bool:
    return chat_id == config.ALLOWED_CHAT_ID


# ---------------------------------------------------------------------------
# Приветствие новых участников
# ---------------------------------------------------------------------------

@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_member_join(event: ChatMemberUpdated, bot: Bot):
    if not _is_allowed_chat(event.chat.id):
        return

    user = event.new_chat_member.user
    await db.ensure_member(user.id, user.username, user.full_name)

    if await db.get_bool_setting("welcome_enabled"):
        template = await db.get_setting("welcome_text")
        text = template.format(chat_title=event.chat.title or "чат", user_mention=user.mention_html())
        try:
            await bot.send_message(event.chat.id, text)
        except Exception as e:
            logger.warning(f"Не удалось отправить приветствие: {e}")

    await db.add_log("join", user.id, None, "")


# ---------------------------------------------------------------------------
# Админ-панель
# ---------------------------------------------------------------------------

@router.message(Command("settings"))
async def cmd_settings(message: Message, bot: Bot):
    if not _is_allowed_chat(message.chat.id):
        return
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in ("creator", "administrator"):
        await message.reply("⛔ Эта команда доступна только администраторам.")
        return
    await message.reply("🛠 <b>Админ-панель @RewchikChat</b>\nВыберите раздел:", reply_markup=keyboards.main_menu())


@router.message(Command("rules"))
async def cmd_rules(message: Message):
    if not _is_allowed_chat(message.chat.id):
        return
    text = (
        "📋 <b>Правила чата @RewchikChat</b>\n\n"
        "1. Уважайте других участников.\n"
        "2. Без рекламы и спама.\n"
        "3. Без флуда и капса.\n"
        "4. Соблюдайте тематику чата.\n\n"
        "Нарушения фиксируются автоматически: варн → мут → бан."
    )
    await message.reply(text)


# ---------------------------------------------------------------------------
# Ручные команды модерации (через reply на сообщение нарушителя)
# ---------------------------------------------------------------------------

async def _require_admin_and_target(message: Message, bot: Bot):
    if not _is_allowed_chat(message.chat.id):
        return None
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in ("creator", "administrator"):
        await message.reply("⛔ Эта команда доступна только администраторам.")
        return None
    if not message.reply_to_message:
        await message.reply("ℹ️ Ответьте этой командой на сообщение нужного участника.")
        return None
    return message.reply_to_message.from_user


@router.message(Command("warn"))
async def cmd_warn(message: Message, bot: Bot, command: CommandObject):
    target = await _require_admin_and_target(message, bot)
    if not target:
        return
    reason = command.args or "без причины"
    await db.ensure_member(target.id, target.username, target.full_name)
    action, extra = await punishments.apply_verdict(bot, message.chat.id, target.id, "manual", reason, message.from_user.id)
    await message.reply(f"⚠️ Пользователю {target.mention_html()} вынесено предупреждение.\nПричина: {reason}")


@router.message(Command("mute"))
async def cmd_mute(message: Message, bot: Bot, command: CommandObject):
    target = await _require_admin_and_target(message, bot)
    if not target:
        return
    minutes = 30
    if command.args:
        try:
            minutes = int(command.args.split()[0])
        except ValueError:
            pass
    await db.ensure_member(target.id, target.username, target.full_name)
    await punishments.mute_user(bot, message.chat.id, target.id, minutes * 60, "manual")
    await message.reply(f"🔇 {target.mention_html()} замучен на {minutes} мин.")


@router.message(Command("unmute"))
async def cmd_unmute(message: Message, bot: Bot):
    target = await _require_admin_and_target(message, bot)
    if not target:
        return
    await punishments.unmute_user(bot, message.chat.id, target.id)
    await message.reply(f"🔊 С {target.mention_html()} снят мут.")


@router.message(Command("kick"))
async def cmd_kick(message: Message, bot: Bot, command: CommandObject):
    target = await _require_admin_and_target(message, bot)
    if not target:
        return
    await punishments.kick_user(bot, message.chat.id, target.id, command.args or "", message.from_user.id)
    await message.reply(f"👢 {target.mention_html()} удалён из чата.")


@router.message(Command("ban"))
async def cmd_ban(message: Message, bot: Bot, command: CommandObject):
    target = await _require_admin_and_target(message, bot)
    if not target:
        return
    await punishments.ban_user(bot, message.chat.id, target.id, command.args or "", message.from_user.id)
    await message.reply(f"🚫 {target.mention_html()} забанен.")


@router.message(Command("unban"))
async def cmd_unban(message: Message, bot: Bot):
    target = await _require_admin_and_target(message, bot)
    if not target:
        return
    await punishments.unban_user(bot, message.chat.id, target.id, message.from_user.id)
    await message.reply(f"✅ {target.mention_html()} разбанен.")


@router.message(Command("setflood"))
async def cmd_setflood(message: Message, bot: Bot, command: CommandObject):
    if not _is_allowed_chat(message.chat.id):
        return
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in ("creator", "administrator"):
        return
    if not command.args:
        await message.reply("Использование: /setflood <лимит> <окно_сек>")
        return
    try:
        limit, window = command.args.split()
        await db.set_setting("antiflood_limit", str(int(limit)))
        await db.set_setting("antiflood_window_sec", str(int(window)))
        await message.reply(f"✅ Антифлуд обновлён: {limit} сообщений / {window} сек.")
    except Exception:
        await message.reply("Использование: /setflood <лимит> <окно_сек>")


@router.message(Command("setwelcome"))
async def cmd_setwelcome(message: Message, bot: Bot, command: CommandObject):
    if not _is_allowed_chat(message.chat.id):
        return
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in ("creator", "administrator"):
        return
    if not command.args:
        await message.reply("Использование: /setwelcome <текст с {chat_title} и {user_mention}>")
        return
    await db.set_setting("welcome_text", command.args)
    await message.reply("✅ Текст приветствия обновлён.")


@router.message(Command("rank"))
async def cmd_rank(message: Message):
    if not _is_allowed_chat(message.chat.id):
        return
    m = await db.get_member(message.from_user.id)
    if not m:
        await message.reply("Данных пока нет — напишите пару сообщений в чат.")
        return
    await message.reply(
        f"📈 <b>{message.from_user.full_name}</b>\n"
        f"Уровень: {m['level']}\nXP: {m['xp']}\nСообщений: {m['message_count']}\nВарнов: {m['warns_count']}"
    )


# ---------------------------------------------------------------------------
# Обработка обычных сообщений: модерация + XP
# ---------------------------------------------------------------------------

@router.message(F.text | F.caption)
async def on_message(message: Message, bot: Bot):
    if not _is_allowed_chat(message.chat.id):
        return
    if message.from_user.is_bot:
        return

    member_status = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if member_status.status in ("creator", "administrator"):
        # Админов не модерируем, но считаем сообщения/XP
        await db.ensure_member(message.from_user.id, message.from_user.username, message.from_user.full_name)
        await db.bump_message_count(message.from_user.id)
        return

    await db.ensure_member(message.from_user.id, message.from_user.username, message.from_user.full_name)
    text = message.text or message.caption or ""

    urls = []
    entities = message.entities or message.caption_entities or []
    for ent in entities:
        if ent.type == "url":
            urls.append(text[ent.offset: ent.offset + ent.length])
        elif ent.type == "text_link" and ent.url:
            urls.append(ent.url)

    dbmember = await db.get_member(message.from_user.id)
    joined_at = dbmember["joined_at"] if dbmember else None

    verdicts = [
        await moderation.check_antiflood(message.from_user.id),
        await moderation.check_antispam_duplicate(message.from_user.id, text, joined_at),
        await moderation.check_banned_words(text),
        await moderation.check_antiad(text, urls),
    ]
    final_verdict = moderation.strongest_verdict(verdicts)

    if final_verdict.triggered:
        try:
            await message.delete()
        except Exception as e:
            logger.warning(f"Не удалось удалить сообщение: {e}")

        action, extra = await punishments.apply_verdict(
            bot, message.chat.id, message.from_user.id, final_verdict.module, final_verdict.reason
        )

        notice = {
            "warn": f"⚠️ {message.from_user.mention_html()}, предупреждение: {final_verdict.reason}",
            "mute": f"🔇 {message.from_user.mention_html()} замучен ({extra // 60} мин.). Причина: {final_verdict.reason}",
            "ban": f"🚫 {message.from_user.mention_html()} забанен за повторные нарушения.",
        }[action]
        try:
            sent = await bot.send_message(message.chat.id, notice)
            await asyncio.sleep(8)
            await sent.delete()
        except Exception:
            pass
        return

    new_level = await db.bump_message_count(message.from_user.id)
    if new_level is not None and new_level > 0:
        try:
            await bot.send_message(
                message.chat.id,
                f"🎉 {message.from_user.mention_html()} достиг {new_level} уровня!",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

async def main():
    if not config.BOT_TOKEN or config.BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError(
            "BOT_TOKEN не задан. На Railway: Project -> Variables -> добавьте BOT_TOKEN. "
            "Локально: заполните .env (см. .env.example)."
        )

    await db.init_db()

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    dp.include_router(panel_router)

    logger.info("Бот запущен. Разрешённый чат: %s (%s)", config.ALLOWED_CHAT_USERNAME, config.ALLOWED_CHAT_ID)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    finally:
        await db.close_db()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
