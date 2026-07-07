"""
Точка входа бота @RewchikChat.

Бот работает ИСКЛЮЧИТЕЛЬНО в чате config.ALLOWED_CHAT_ID.
Если бота добавляют в любой другой чат — он присылает сообщение и сразу выходит
(middleware RestrictToSingleChat ниже перехватывает это до любых других обработчиков).
"""
import asyncio
import logging
import random

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message, ChatMemberUpdated, CallbackQuery
from aiogram.types import ChatPermissions
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.filters.chat_member_updated import ChatMemberUpdatedFilter, JOIN_TRANSITION, LEAVE_TRANSITION

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


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я бот чата @RewchikChat.\n\n"
        "📋 Мои команды:\n"
        "/rules — правила чата\n"
        "/rank — твой уровень и статистика\n"
        "/top — топ участников\n"
        "/rep — дать репутацию (ответом на сообщение)\n\n"
        "Остальные функции работают в группе @RewchikChat."
    )

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
    # Личные сообщения (chat_id > 0) разрешены для информационных команд (см. cmd_start).
    return chat_id > 0 or chat_id == config.ALLOWED_CHAT_ID


def _is_group_chat(chat_id: int) -> bool:
    """Строгая проверка для всего, что требует прав/контекста группы: модерация, капча,
    вход/выход участников, админ-команды (используют bot.get_chat_member, что не имеет
    смысла в личных сообщениях)."""
    return chat_id == config.ALLOWED_CHAT_ID


# ---------------------------------------------------------------------------
# Приветствие, капча для новичков и прощание
# ---------------------------------------------------------------------------

RESTRICTED_ON_JOIN = ChatPermissions(
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

FULL_PERMISSIONS = ChatPermissions(
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


def _captcha_keyboard(user_id: int, correct: int, options: list[int]):
    b = InlineKeyboardBuilder()
    for opt in options:
        b.button(text=str(opt), callback_data=f"captcha:{user_id}:{opt}:{correct}")
    b.adjust(len(options))
    return b.as_markup()


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_member_join(event: ChatMemberUpdated, bot: Bot):
    if not _is_group_chat(event.chat.id):
        return

    user = event.new_chat_member.user
    await db.ensure_member(user.id, user.username, user.full_name)
    await db.add_log("join", user.id, None, "")

    if user.is_bot:
        return

    if not await db.get_bool_setting("captcha_enabled"):
        await _send_welcome(bot, event.chat.id, event.chat.title, user)
        return

    # Ограничиваем новичка до прохождения капчи (простая проверка "не робот" через выбор числа)
    try:
        await bot.restrict_chat_member(event.chat.id, user.id, permissions=RESTRICTED_ON_JOIN)
    except Exception as e:
        logger.warning(f"Не удалось ограничить новичка {user.id} на время капчи: {e}")

    a, b_ = random.randint(1, 9), random.randint(1, 9)
    correct = a + b_
    options = {correct}
    while len(options) < 4:
        options.add(random.randint(2, 18))
    options = list(options)
    random.shuffle(options)

    timeout = int(await db.get_setting("captcha_timeout_sec"))
    deadline = int(asyncio.get_event_loop().time()) + timeout

    try:
        sent = await bot.send_message(
            event.chat.id,
            f"🤖 {user.mention_html()}, подтвердите, что вы не робот.\n"
            f"Сколько будет <b>{a} + {b_}</b>? У вас {timeout // 60} мин., иначе вы будете удалены из чата.",
            reply_markup=_captcha_keyboard(user.id, correct, options),
        )
    except Exception as e:
        logger.warning(f"Не удалось отправить капчу: {e}")
        return

    await db.add_pending_captcha(user.id, sent.message_id, str(correct), deadline)
    asyncio.create_task(_captcha_timeout_kick(bot, event.chat.id, user.id, sent.message_id, timeout))


async def _captcha_timeout_kick(bot: Bot, chat_id: int, user_id: int, captcha_message_id: int, timeout: int):
    await asyncio.sleep(timeout)
    pending = await db.get_pending_captcha(user_id)
    if pending is None:
        return  # уже прошёл капчу
    await db.remove_pending_captcha(user_id)
    try:
        await bot.delete_message(chat_id, captcha_message_id)
    except Exception:
        pass
    try:
        await bot.ban_chat_member(chat_id, user_id)
        await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)  # кик, а не бан навсегда
    except Exception as e:
        logger.warning(f"Не удалось удалить не прошедшего капчу {user_id}: {e}")
    await db.add_log("captcha_timeout_kick", user_id, None, "")


@router.callback_query(F.data.startswith("captcha:"))
async def on_captcha_answer(callback: CallbackQuery, bot: Bot):
    _, target_user_id, chosen, correct = callback.data.split(":")
    target_user_id, chosen, correct = int(target_user_id), int(chosen), int(correct)

    if callback.from_user.id != target_user_id:
        await callback.answer("Эта капча не для вас 🙂", show_alert=True)
        return

    pending = await db.get_pending_captcha(target_user_id)
    if pending is None:
        await callback.answer("Капча уже неактивна.", show_alert=True)
        return

    chat_id = callback.message.chat.id

    if chosen != correct:
        await callback.answer("❌ Неверно, попробуйте ещё раз или дождитесь новой капчи.", show_alert=True)
        return

    await db.remove_pending_captcha(target_user_id)
    try:
        await bot.restrict_chat_member(chat_id, target_user_id, permissions=FULL_PERMISSIONS)
    except Exception as e:
        logger.warning(f"Не удалось снять ограничение после капчи: {e}")

    try:
        await callback.message.delete()
    except Exception:
        pass

    await db.add_log("captcha_passed", target_user_id, None, "")
    await _send_welcome(bot, chat_id, callback.message.chat.title, callback.from_user)
    await callback.answer("✅ Добро пожаловать!")


async def _send_welcome(bot: Bot, chat_id: int, chat_title: str, user):
    if not await db.get_bool_setting("welcome_enabled"):
        return
    template = await db.get_setting("welcome_text")
    text = template.format(chat_title=chat_title or "чат", user_mention=user.mention_html())
    try:
        await bot.send_message(chat_id, text)
    except Exception as e:
        logger.warning(f"Не удалось отправить приветствие: {e}")


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=LEAVE_TRANSITION))
async def on_member_leave(event: ChatMemberUpdated, bot: Bot):
    if not _is_group_chat(event.chat.id):
        return
    user = event.old_chat_member.user
    if user.is_bot:
        return
    await db.add_log("leave", user.id, None, "")
    if await db.get_bool_setting("farewell_enabled"):
        template = await db.get_setting("farewell_text")
        text = template.format(chat_title=event.chat.title or "чат", user_mention=user.mention_html())
        try:
            await bot.send_message(event.chat.id, text)
        except Exception as e:
            logger.warning(f"Не удалось отправить прощание: {e}")


# ---------------------------------------------------------------------------
# Админ-панель
# ---------------------------------------------------------------------------

@router.message(Command("settings"))
async def cmd_settings(message: Message, bot: Bot):
    if not _is_group_chat(message.chat.id):
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


@router.message(Command("stats"))
async def cmd_stats(message: Message, bot: Bot):
    if not _is_group_chat(message.chat.id):
        return
    role = await _get_effective_role(bot, message.chat.id, message.from_user.id)
    if role == "member":
        await message.reply("⛔ Статистика доступна администраторам и модераторам.")
        return
    o = await db.chat_overview()
    await message.reply(
        "📊 <b>Обзор чата @RewchikChat</b>\n\n"
        f"👥 Участников в базе: {o['total_members']}\n"
        f"🔇 Сейчас замучено: {o['muted']}\n"
        f"🚫 Забанено: {o['banned']}\n"
        f"💬 Всего сообщений учтено: {o['total_messages']}\n"
        f"⚠️ Нарушений за 24 часа: {o['violations_24h']}"
    )


# ---------------------------------------------------------------------------
# Ручные команды модерации (через reply на сообщение нарушителя)
# ---------------------------------------------------------------------------

async def _require_admin_and_target(message: Message, bot: Bot):
    if not _is_group_chat(message.chat.id):
        return None
    member = await bot.get_chat_member(message.chat.id, message.from_user.id)
    if member.status not in ("creator", "administrator"):
        await message.reply("⛔ Эта команда доступна только администраторам.")
        return None
    if not message.reply_to_message:
        await message.reply("ℹ️ Ответьте этой командой на сообщение нужного участника.")
        return None
    return message.reply_to_message.from_user


async def _get_effective_role(bot: Bot, chat_id: int, user_id: int) -> str:
    """admin — реальный Telegram-админ/создатель; moderator — кастомная роль из БД; member — все остальные."""
    member = await bot.get_chat_member(chat_id, user_id)
    if member.status in ("creator", "administrator"):
        return "admin"
    role = await db.get_role(user_id)
    if role == config.ROLE_MODERATOR:
        return "moderator"
    return "member"


async def _require_moderator_and_target(message: Message, bot: Bot):
    """Как _require_admin_and_target, но пропускает также пользователей с кастомной ролью 'модератор'
    (может варнить/мутить/снимать мут, но не кикать/банить/менять настройки — см. раздел 3.7 ТЗ)."""
    if not _is_group_chat(message.chat.id):
        return None
    role = await _get_effective_role(bot, message.chat.id, message.from_user.id)
    if role == "member":
        await message.reply("⛔ Эта команда доступна администраторам и модераторам.")
        return None
    if not message.reply_to_message:
        await message.reply("ℹ️ Ответьте этой командой на сообщение нужного участника.")
        return None
    return message.reply_to_message.from_user


@router.message(Command("warn"))
async def cmd_warn(message: Message, bot: Bot, command: CommandObject):
    target = await _require_moderator_and_target(message, bot)
    if not target:
        return
    reason = command.args or "без причины"
    await db.ensure_member(target.id, target.username, target.full_name)
    action, extra = await punishments.apply_verdict(bot, message.chat.id, target.id, "manual", reason, message.from_user.id)
    await message.reply(f"⚠️ Пользователю {target.mention_html()} вынесено предупреждение.\nПричина: {reason}")


@router.message(Command("mute"))
async def cmd_mute(message: Message, bot: Bot, command: CommandObject):
    target = await _require_moderator_and_target(message, bot)
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
    target = await _require_moderator_and_target(message, bot)
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
    if not _is_group_chat(message.chat.id):
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
    if not _is_group_chat(message.chat.id):
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
    rep = await db.get_reputation(message.from_user.id)
    lines = [
        f"📈 <b>{message.from_user.full_name}</b>",
        f"Уровень: {m['level']}\nXP: {m['xp']}\nСообщений: {m['message_count']}",
        f"Варнов: {m['warns_count']}\nРепутация: {rep}",
    ]
    if await db.get_bool_setting("economy_enabled"):
        balance = await db.get_balance(message.from_user.id)
        currency = await db.get_setting("currency_name")
        lines.append(f"Баланс: {balance} {currency}")
    await message.reply("\n".join(lines))


@router.message(Command("promote"))
async def cmd_promote(message: Message, bot: Bot):
    """Выдать кастомную роль 'модератор' (только настоящие админы/создатель)."""
    target = await _require_admin_and_target(message, bot)
    if not target:
        return
    await db.ensure_member(target.id, target.username, target.full_name)
    await db.grant_role(target.id, config.ROLE_MODERATOR, message.from_user.id)
    await db.add_log("promote", target.id, message.from_user.id, "role=moderator")
    await message.reply(f"⭐ {target.mention_html()} назначен(а) модератором чата.")


@router.message(Command("demote"))
async def cmd_demote(message: Message, bot: Bot):
    target = await _require_admin_and_target(message, bot)
    if not target:
        return
    await db.revoke_role(target.id)
    await db.add_log("demote", target.id, message.from_user.id, "")
    await message.reply(f"➖ {target.mention_html()} больше не модератор.")


@router.message(Command("mods"))
async def cmd_mods(message: Message):
    if not _is_allowed_chat(message.chat.id):
        return
    rows = await db.list_roles(config.ROLE_MODERATOR)
    if not rows:
        await message.reply("Пока нет назначенных модераторов (кроме администраторов Telegram).")
        return
    lines = ["🎭 <b>Модераторы чата</b>", ""]
    for r in rows:
        m = await db.get_member(r["user_id"])
        name = (m["username"] or m["full_name"]) if m else str(r["user_id"])
        lines.append(f"• @{name}")
    await message.reply("\n".join(lines))


@router.message(Command("rep"))
async def cmd_rep(message: Message):
    if not _is_allowed_chat(message.chat.id):
        return
    if not await db.get_bool_setting("reputation_enabled"):
        await message.reply("Модуль репутации отключён.")
        return
    if not message.reply_to_message:
        await message.reply("ℹ️ Ответьте командой /rep на сообщение участника, которому хотите начислить репутацию.")
        return
    target = message.reply_to_message.from_user
    if target.id == message.from_user.id:
        await message.reply("Нельзя начислить репутацию самому себе 🙂")
        return
    if target.is_bot:
        await message.reply("Ботам репутация не начисляется.")
        return

    cooldown = int(await db.get_setting("reputation_cooldown_sec"))
    await db.ensure_member(target.id, target.username, target.full_name)
    success, score = await db.add_reputation(target.id, message.from_user.id, cooldown)
    if not success:
        await message.reply(f"⏳ Вы уже начисляли репутацию {target.mention_html()} недавно. Попробуйте позже.")
        return
    await message.reply(f"⭐ {target.mention_html()} получил(а) +1 к репутации! Теперь: {score}")


@router.message(Command("top"))
async def cmd_top(message: Message):
    if not _is_allowed_chat(message.chat.id):
        return
    xp_rows = await db.top_xp(10)
    rep_rows = await db.top_reputation(10)

    lines = ["📊 <b>Топ участников по активности (XP)</b>", ""]
    for i, r in enumerate(xp_rows, 1):
        name = r["username"] or r["full_name"] or r["user_id"]
        lines.append(f"{i}. @{name} — уровень {r['level']}, {r['xp']} XP")

    if rep_rows:
        lines += ["", "⭐ <b>Топ по репутации</b>", ""]
        for i, r in enumerate(rep_rows, 1):
            name = r["username"] or r["full_name"] or r["user_id"]
            lines.append(f"{i}. @{name} — {r['score']}")

    if await db.get_bool_setting("economy_enabled"):
        balance_rows = await db.top_balance(10)
        if balance_rows:
            currency = await db.get_setting("currency_name")
            lines += [f"", f"💰 <b>Топ по балансу ({currency})</b>", ""]
            for i, r in enumerate(balance_rows, 1):
                name = r["username"] or r["full_name"] or r["user_id"]
                lines.append(f"{i}. @{name} — {r['balance']}")

    await message.reply("\n".join(lines))


# ---------------------------------------------------------------------------
# Экономика, достижения, мини-игры
# ---------------------------------------------------------------------------

@router.message(Command("balance"))
async def cmd_balance(message: Message):
    if not _is_allowed_chat(message.chat.id):
        return
    if not await db.get_bool_setting("economy_enabled"):
        await message.reply("Экономика в этом чате отключена.")
        return
    await db.ensure_member(message.from_user.id, message.from_user.username, message.from_user.full_name)
    starting = int(await db.get_setting("starting_balance"))
    await db.ensure_wallet(message.from_user.id, starting)
    balance = await db.get_balance(message.from_user.id)
    currency = await db.get_setting("currency_name")
    await message.reply(f"💰 Ваш баланс: <b>{balance} {currency}</b>")


@router.message(Command("daily"))
async def cmd_daily(message: Message):
    if not _is_allowed_chat(message.chat.id):
        return
    if not await db.get_bool_setting("economy_enabled"):
        await message.reply("Экономика в этом чате отключена.")
        return
    await db.ensure_member(message.from_user.id, message.from_user.username, message.from_user.full_name)
    starting = int(await db.get_setting("starting_balance"))
    await db.ensure_wallet(message.from_user.id, starting)

    amount = int(await db.get_setting("daily_bonus_amount"))
    cooldown = int(await db.get_setting("daily_bonus_cooldown_sec"))
    currency = await db.get_setting("currency_name")

    success, remaining = await db.try_claim_daily(message.from_user.id, amount, cooldown)
    if not success:
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        await message.reply(f"⏳ Ежедневный бонус уже получен. Приходите через {hours} ч {minutes} мин.")
        return

    balance = await db.get_balance(message.from_user.id)
    await message.reply(f"🎁 Ежедневный бонус получен: +{amount} {currency}!\nБаланс: {balance} {currency}")


@router.message(Command("give"))
async def cmd_give(message: Message, bot: Bot, command: CommandObject):
    """Выдать монеты участнику (только администраторы)."""
    target = await _require_admin_and_target(message, bot)
    if not target:
        return
    if not command.args:
        await message.reply("Использование: /give <количество> (ответом на сообщение участника)")
        return
    try:
        amount = int(command.args.split()[0])
    except ValueError:
        await message.reply("Использование: /give <количество>")
        return

    await db.ensure_member(target.id, target.username, target.full_name)
    starting = int(await db.get_setting("starting_balance"))
    await db.ensure_wallet(target.id, starting)
    new_balance = await db.add_balance(target.id, amount, f"admin_grant:{message.from_user.id}")
    await db.add_log("give", target.id, message.from_user.id, f"amount={amount}")
    currency = await db.get_setting("currency_name")
    await message.reply(f"✅ {target.mention_html()} получил(а) {amount} {currency}. Баланс: {new_balance}")


@router.message(Command("achievements"))
async def cmd_achievements(message: Message):
    if not _is_allowed_chat(message.chat.id):
        return
    earned = await db.get_user_achievements(message.from_user.id)
    all_ach = await db.get_all_achievements()
    earned_keys = {a["key"] for a in earned}

    lines = ["🏆 <b>Достижения</b>", ""]
    for ach in all_ach:
        mark = "✅" if ach["key"] in earned_keys else "🔒"
        lines.append(f"{mark} {ach['title']} — {ach['description']}")
    await message.reply("\n".join(lines))


@router.message(Command("dice"))
async def cmd_dice(message: Message, command: CommandObject):
    """Мини-игра: ставка на кубик 1-6. Выпало 4-6 — выигрыш x2, иначе ставка сгорает."""
    if not _is_allowed_chat(message.chat.id):
        return
    if not await db.get_bool_setting("economy_enabled") or not await db.get_bool_setting("minigames_enabled"):
        await message.reply("Мини-игры сейчас отключены.")
        return
    if not command.args:
        await message.reply("Использование: /dice <ставка>")
        return
    try:
        bet = int(command.args.split()[0])
    except ValueError:
        await message.reply("Использование: /dice <ставка>")
        return

    min_bet = int(await db.get_setting("dice_min_bet"))
    max_bet = int(await db.get_setting("dice_max_bet"))
    currency = await db.get_setting("currency_name")

    if bet < min_bet or bet > max_bet:
        await message.reply(f"Ставка должна быть от {min_bet} до {max_bet} {currency}.")
        return

    await db.ensure_member(message.from_user.id, message.from_user.username, message.from_user.full_name)
    starting = int(await db.get_setting("starting_balance"))
    await db.ensure_wallet(message.from_user.id, starting)
    balance = await db.get_balance(message.from_user.id)
    if balance < bet:
        await message.reply(f"Недостаточно средств. Ваш баланс: {balance} {currency}.")
        return

    roll_msg = await message.answer_dice(emoji="🎲")
    await asyncio.sleep(3)  # дать анимации кубика доиграть перед оглашением результата
    value = roll_msg.dice.value  # 1..6

    if value >= 4:
        winnings = bet  # чистый выигрыш (итого возвращается ставка + столько же)
        new_balance = await db.add_balance(message.from_user.id, winnings, "dice_win")
        await message.reply(f"🎲 Выпало {value}! Вы выиграли {winnings} {currency}.\nБаланс: {new_balance}")
    else:
        new_balance = await db.add_balance(message.from_user.id, -bet, "dice_loss")
        await message.reply(f"🎲 Выпало {value}. Вы проиграли {bet} {currency}.\nБаланс: {new_balance}")


# ---------------------------------------------------------------------------
# Обработка обычных сообщений: модерация + XP
# ---------------------------------------------------------------------------

async def _process_engagement(bot: Bot, chat_id: int, user):
    """Общий хук вовлечения после засчитанного сообщения: XP/уровень, монеты за сообщение, достижения."""
    new_level = await db.bump_message_count(user.id)

    if await db.get_bool_setting("economy_enabled"):
        reward = int(await db.get_setting("message_reward"))
        cooldown = int(await db.get_setting("message_reward_cooldown_sec"))
        await db.try_claim_message_reward(user.id, reward, cooldown)

    if new_level is not None and new_level > 0:
        bonus_text = ""
        if await db.get_bool_setting("economy_enabled"):
            bonus = int(await db.get_setting("levelup_bonus_amount"))
            await db.add_balance(user.id, bonus, "levelup_bonus")
            currency = await db.get_setting("currency_name")
            bonus_text = f" (+{bonus} {currency})"
        try:
            await bot.send_message(chat_id, f"🎉 {user.mention_html()} достиг {new_level} уровня!{bonus_text}")
        except Exception:
            pass

    for ach in await db.check_and_award_achievements(user.id):
        try:
            await bot.send_message(
                chat_id, f"🏆 {user.mention_html()} получил(а) достижение «{ach['title']}»!\n{ach['description']}"
            )
        except Exception:
            pass


@router.message(F.text | F.caption)
async def on_message(message: Message, bot: Bot):
    if not _is_group_chat(message.chat.id):
        return
    if message.from_user.is_bot:
        return

    # Если у пользователя ожидает подтверждения капча — любое сообщение до её прохождения удаляем
    pending_captcha = await db.get_pending_captcha(message.from_user.id)
    if pending_captcha is not None:
        try:
            await message.delete()
        except Exception:
            pass
        return

    role = await _get_effective_role(bot, message.chat.id, message.from_user.id)
    await db.ensure_member(message.from_user.id, message.from_user.username, message.from_user.full_name)

    if role in ("admin", "moderator"):
        # Админов и модераторов не модерируем, но считаем сообщения/XP/монеты
        await _process_engagement(bot, message.chat.id, message.from_user)
        return

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
        await moderation.check_anticaps(text),
        await moderation.check_antimention(entities),
        await moderation.check_antirepeat(text),
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

    await _process_engagement(bot, message.chat.id, message.from_user)


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
