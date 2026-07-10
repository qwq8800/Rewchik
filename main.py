"""
Точка входа бота @RewchikChat.

Бот работает ИСКЛЮЧИТЕЛЬНО в чате config.ALLOWED_CHAT_ID.
Если бота добавляют в любой другой чат — он присылает сообщение и сразу выходит
(middleware RestrictToSingleChat ниже перехватывает это до любых других обработчиков).
"""
import asyncio
import html
import logging
import random
import time
from collections import deque

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message, ChatMemberUpdated, CallbackQuery, BufferedInputFile
from aiogram.types import ChatPermissions
from aiogram.enums import ContentType
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

# Ожидающие подтверждения смены имени: user_id -> предложенный никнейм.
# Простое хранилище в памяти процесса достаточно — подтверждение происходит
# в течение той же сессии, а не через рестарты (в отличие от мутов/капчи).
_pending_nicknames: dict[int, str] = {}

# Ожидающие ответа дуэли: duel_id -> {"initiator_id", "target_id", "bet"}.
# Аналогично никнеймам — недолговечное состояние, рестарт процесса просто
# отменяет незавершённые вызовы, деньги при этом не списываются заранее.
_pending_duels: dict[int, dict] = {}
_duel_counter = 0

# Метки времени последних вступлений — для детекции рейда (антирейд, раздел 4 ТЗ).
# Как и антифлуд/антиспам, это "горячие" данные, для одного чата достаточно памяти процесса.
_recent_joins: deque = deque(maxlen=200)


# ---------------------------------------------------------------------------
# Ограничение бота одним чатом
# ---------------------------------------------------------------------------


@router.message(CommandStart())
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я бот чата @RewchikChat.\n\n"
        "📋 Полный список команд: /help\n"
        "📜 Правила чата: /rules\n\n"
        "Модерация и админ-функции работают только в самой группе @RewchikChat."
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    if not _is_allowed_chat(message.chat.id):
        return
    text = (
        "📋 <b>Все команды бота</b>\n\n"

        "👤 <b>Общие</b>\n"
        "/rules — правила чата\n"
        "/help — этот список команд\n"
        "/rank — свой уровень, XP, репутация, баланс\n"
        "/setname &lt;имя&gt; — задать имя, которым бот будет тегать вас в чате (раз в 2 суток, с подтверждением)\n"
        "/top — топ участников по XP, репутации и балансу\n"
        "/rep — начислить +1 репутации (ответом на сообщение, раз в час)\n"
        "/mods — список назначенных модераторов\n"
        "/roles — список ролей чата и их прав\n"
        "/report [причина] — пожаловаться на сообщение (ответом)\n"
        "/achievements — список достижений и прогресс\n\n"

        "💰 <b>Экономика</b>\n"
        "/balance — узнать баланс\n"
        "/daily — забрать ежедневный бонус\n"
        "/shop — магазин косметических бейджей\n"
        "/buy &lt;код&gt; — купить товар\n"
        "/inventory — ваш инвентарь\n"
        "/equip &lt;код&gt; — экипировать бейдж (виден в /rank)\n"
        "/pay &lt;сумма&gt; — перевести монеты участнику (ответом на его сообщение)\n\n"

        "🎮 <b>Мини-игры</b>\n"
        "/dice &lt;ставка&gt; — кубик, честные 50/50\n"
        "/coinflip &lt;ставка&gt; &lt;орёл|решка&gt; — монетка, честные 50/50\n"
        "/slots &lt;ставка&gt; — слот-машина\n"
        "/duel &lt;ставка&gt; — вызов на дуэль (ответом на сообщение соперника)\n\n"

        "🛡 <b>Для модераторов</b> (право «moderate» — по умолчанию у роли «модератор»)\n"
        "/warn, /mute [минуты], /unmute — ответом на сообщение нарушителя\n"
        "/unwarn — снять последнее предупреждение (ответом)\n"
        "/whois — полный профиль участника (ответом)\n"
        "/stats — обзор чата (право «view_stats»)\n\n"

        "👑 <b>Только для администраторов</b>\n"
        "/settings — кнопочная админ-панель\n"
        "/kick, /ban, /unban — ответом на сообщение (право «kick_ban»)\n"
        "/clearwarns — снять ВСЕ предупреждения (право «kick_ban»)\n"
        "/promote, /demote — быстрый ярлык для роли «модератор»\n"
        "/give &lt;количество&gt; — выдать монеты участнику\n"
        "/createrole, /setrole, /removerole — гибкие роли с правами\n"
        "/setflood, /setwelcome, /setwarnexpiry — настройки модерации\n"
        "/addword, /delword, /words — стоп-слова\n"
        "/adddomain, /deldomain, /domains — чёрный список доменов\n"
        "/lockdown, /unlock — экстренная блокировка чата (антирейд)\n"
        "/exportlogs [N] — выгрузить последние N записей лога файлом (право «manage_settings»)"
    )
    await message.reply(text)

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


async def _check_raid(bot: Bot, chat_id: int, chat_title: str):
    """Считает вступления за скользящее окно; при превышении порога — тревога и,
    если включено, автоматическая блокировка чата (антирейд, раздел 4 ТЗ)."""
    if not await db.get_bool_setting("raid_detection_enabled"):
        return
    if await db.get_bool_setting("lockdown_active"):
        return  # уже в блокировке, не спамим повторными тревогами

    window = int(await db.get_setting("raid_window_sec"))
    threshold = int(await db.get_setting("raid_join_threshold"))
    now = time.time()
    while _recent_joins and now - _recent_joins[0] > window:
        _recent_joins.popleft()

    if len(_recent_joins) < threshold:
        return

    await db.add_log("raid_detected", 0, None, f"joins={len(_recent_joins)} window={window}s")
    auto_lockdown = await db.get_bool_setting("raid_auto_lockdown_enabled")

    if auto_lockdown:
        engaged = await punishments.engage_lockdown(bot, chat_id, "автообнаружение рейда")
        status_line = (
            "🔒 Чат автоматически заблокирован (новые сообщения от участников без прав администратора "
            "запрещены) до команды /unlock."
            if engaged else "⚠️ Не удалось автоматически заблокировать чат — проверьте права бота."
        )
    else:
        status_line = "Автоблокировка отключена в настройках — при необходимости включите вручную: /lockdown"

    try:
        await bot.send_message(
            chat_id,
            f"🚨 <b>Похоже на рейд</b>: {len(_recent_joins)} новых участников за последние {window} сек.\n{status_line}",
        )
    except Exception as e:
        logger.warning(f"Не удалось отправить тревогу о рейде: {e}")


@router.chat_member(ChatMemberUpdatedFilter(member_status_changed=JOIN_TRANSITION))
async def on_member_join(event: ChatMemberUpdated, bot: Bot):
    if not _is_group_chat(event.chat.id):
        return

    user = event.new_chat_member.user
    await db.ensure_member(user.id, user.username, user.full_name)
    await db.add_log("join", user.id, None, "")

    _recent_joins.append(time.time())
    await _check_raid(bot, event.chat.id, event.chat.title)

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
    deadline = int(time.time()) + timeout

    try:
        sent = await bot.send_message(
            event.chat.id,
            f"🤖 {await display_mention(user)}, подтвердите, что вы не робот.\n"
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
    text = template.format(chat_title=chat_title or "чат", user_mention=await display_mention(user))
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
        text = template.format(chat_title=event.chat.title or "чат", user_mention=await display_mention(user))
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
    if not await _is_full_admin(bot, message.chat.id, message.from_user.id):
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
    if not await _has_permission(bot, message.chat.id, message.from_user.id, "view_stats"):
        await message.reply("⛔ Статистика доступна администраторам и ролям с правом «Смотреть статистику».")
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


@router.message(Command("whois"))
async def cmd_whois(message: Message, bot: Bot):
    """Полный профиль участника для модерации (ответом на его сообщение)."""
    if not _is_group_chat(message.chat.id):
        return
    if not await _has_permission(bot, message.chat.id, message.from_user.id, "view_stats"):
        await message.reply("⛔ Эта команда доступна администраторам и ролям с правом «Смотреть статистику».")
        return
    if not message.reply_to_message:
        await message.reply("ℹ️ Ответьте командой /whois на сообщение участника.")
        return

    target = message.reply_to_message.from_user
    await db.ensure_member(target.id, target.username, target.full_name)
    m = await db.get_member(target.id)

    role_key = await db.get_role(target.id)
    role_title = "—"
    if role_key:
        role = await db.get_custom_role(role_key)
        role_title = role["title"] if role else role_key

    rep = await db.get_reputation(target.id)
    joined = time.strftime("%d.%m.%Y", time.localtime(m["joined_at"])) if m["joined_at"] else "неизвестно"
    active_warns = await db.count_active_warnings(target.id)

    if m["is_banned"]:
        status = "🚫 забанен"
    elif m["is_muted"]:
        remaining = max(0, m["muted_until"] - int(time.time()))
        status = f"🔇 замучен ещё {remaining // 60} мин."
    else:
        status = "✅ обычный статус"

    lines = [
        f"🔍 <b>Профиль</b>: {await display_mention(target)}",
        f"ID: <code>{target.id}</code>",
        f"В чате с: {joined}",
        f"Уровень: {m['level']} (XP {m['xp']})",
        f"Сообщений: {m['message_count']}",
        f"Активных предупреждений: {active_warns} (всего за всё время: {m['warns_count']})",
        f"Репутация: {rep}",
        f"Роль: {role_title}",
        f"Статус: {status}",
    ]
    if await db.get_bool_setting("economy_enabled"):
        balance = await db.get_balance(target.id)
        currency = await db.get_setting("currency_name")
        lines.append(f"Баланс: {balance} {currency}")

    await message.reply("\n".join(lines))


@router.message(Command("exportlogs"))
async def cmd_exportlogs(message: Message, bot: Bot, command: CommandObject):
    """Выгрузить последние N записей лога файлом .txt (раздел «Прозрачность» ТЗ)."""
    if not await _require_permission(message, bot, "manage_settings"):
        return

    limit = 200
    if command.args:
        try:
            limit = min(2000, max(1, int(command.args.split()[0])))
        except ValueError:
            pass

    rows = await db.get_logs(limit=limit, offset=0)
    if not rows:
        await message.reply("Логов пока нет.")
        return

    lines = []
    for r in reversed(rows):  # от старых к новым — удобнее читать как хронологию
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"]))
        lines.append(f"[{ts}] {r['action']} | user={r['user_id']} | moderator={r['moderator_id']} | {r['details']}")

    content = "\n".join(lines)
    filename = f"rewchik_logs_{time.strftime('%Y%m%d_%H%M%S')}.txt"
    doc = BufferedInputFile(content.encode("utf-8"), filename=filename)
    await message.reply_document(doc, caption=f"📄 Экспорт логов: {len(rows)} записей.")


@router.message(Command("report"))
async def cmd_report(message: Message, bot: Bot, command: CommandObject):
    """Жалоба участника на сообщение (ответом). Уведомляет админов/модераторов кнопками для быстрой реакции."""
    if not _is_group_chat(message.chat.id):
        return
    if not message.reply_to_message:
        await message.reply("ℹ️ Ответьте командой /report на проблемное сообщение.")
        return

    target = message.reply_to_message.from_user
    if target.id == message.from_user.id:
        await message.reply("Нельзя пожаловаться на самого себя 🙂")
        return
    if target.is_bot:
        await message.reply("На ботов жаловаться нет смысла 🙂")
        return

    role = await _get_effective_role(bot, message.chat.id, target.id)
    if role in ("admin", "moderator"):
        await message.reply("На администраторов и модераторов пожаловаться через эту команду нельзя.")
        return

    cooldown = int(await db.get_setting("report_cooldown_sec"))
    last = await db.get_last_report_time(message.from_user.id)
    if int(time.time()) - last < cooldown:
        await message.reply("⏳ Вы недавно уже отправляли жалобу. Подождите немного, чтобы не спамить.")
        return

    reason = command.args or "без указания причины"
    snippet_source = message.reply_to_message.text or message.reply_to_message.caption or "(сообщение без текста)"
    snippet = snippet_source[:200]

    await db.ensure_member(target.id, target.username, target.full_name)
    report_id = await db.add_report(message.from_user.id, target.id, message.reply_to_message.message_id, snippet, reason)
    await db.add_log("report", target.id, message.from_user.id, f"report_id={report_id} reason={reason}")

    await message.reply("✅ Жалоба отправлена администраторам и модераторам.")

    try:
        await bot.send_message(
            message.chat.id,
            f"📨 <b>Новая жалоба #{report_id}</b>\n"
            f"От: {await display_mention(message.from_user)}\n"
            f"На: {await display_mention(target)}\n"
            f"Причина: {reason}\n"
            f"Сообщение: <i>{snippet}</i>",
            reply_markup=keyboards.report_action_keyboard(report_id),
        )
    except Exception as e:
        logger.warning(f"Не удалось отправить уведомление о жалобе: {e}")


# ---------------------------------------------------------------------------
# Ручные команды модерации (через reply на сообщение нарушителя)
# ---------------------------------------------------------------------------

async def _require_admin_and_target(message: Message, bot: Bot):
    if not _is_group_chat(message.chat.id):
        return None
    if not await _is_full_admin(bot, message.chat.id, message.from_user.id):
        await message.reply("⛔ Эта команда доступна только администраторам.")
        return None
    if not message.reply_to_message:
        await message.reply("ℹ️ Ответьте этой командой на сообщение нужного участника.")
        return None
    return message.reply_to_message.from_user


async def display_mention(user) -> str:
    """HTML-упоминание пользователя для сообщений бота в чате: если участник задал себе
    кастомное отображаемое имя (/setname), используем его вместо имени из профиля Telegram —
    но ссылка на tg://user всё равно тегает настоящего человека, как обычное упоминание."""
    nickname = await db.get_nickname(user.id)
    if nickname:
        return f'<a href="tg://user?id={user.id}">{html.escape(nickname)}</a>'
    return user.mention_html()


async def _is_full_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Главный админ бота (config.SUPER_ADMIN_ID) имеет все права всегда, независимо
    от того, выдал ли ему Telegram статус admin/creator в самом чате. Помимо него —
    обычная проверка реального статуса creator/administrator."""
    if user_id == config.SUPER_ADMIN_ID:
        return True
    member = await bot.get_chat_member(chat_id, user_id)
    return member.status in ("creator", "administrator")


async def _get_effective_role(bot: Bot, chat_id: int, user_id: int) -> str:
    """admin — реальный Telegram-админ/создатель (или главный админ бота); moderator — назначена
    любая кастомная роль; member — все остальные. Для точечной проверки конкретных прав
    используйте _has_permission()."""
    if await _is_full_admin(bot, chat_id, user_id):
        return "admin"
    role = await db.get_role(user_id)
    if role:
        return "moderator"
    return "member"


async def _has_permission(bot: Bot, chat_id: int, user_id: int, permission: str) -> bool:
    """Реальные Telegram-админы/создатель (и главный админ бота) имеют все права всегда.
    Остальные — только то, что явно указано в permissions их назначенной кастомной роли
    (раздел «Роли» ТЗ)."""
    if await _is_full_admin(bot, chat_id, user_id):
        return True
    return await db.user_has_permission(user_id, permission)


async def _require_permission_and_target(message: Message, bot: Bot, permission: str):
    """Как _require_admin_and_target, но пропускает любого, у чьей роли есть указанное право."""
    if not _is_group_chat(message.chat.id):
        return None
    if not await _has_permission(bot, message.chat.id, message.from_user.id, permission):
        await message.reply(f"⛔ Для этой команды нужно право «{config.PERMISSIONS.get(permission, permission)}».")
        return None
    if not message.reply_to_message:
        await message.reply("ℹ️ Ответьте этой командой на сообщение нужного участника.")
        return None
    return message.reply_to_message.from_user


async def _require_permission(message: Message, bot: Bot, permission: str) -> bool:
    """Проверка права без цели (для команд настройки чата)."""
    if not _is_group_chat(message.chat.id):
        return False
    if not await _has_permission(bot, message.chat.id, message.from_user.id, permission):
        await message.reply(f"⛔ Для этой команды нужно право «{config.PERMISSIONS.get(permission, permission)}».")
        return False
    return True


@router.message(Command("warn"))
async def cmd_warn(message: Message, bot: Bot, command: CommandObject):
    target = await _require_permission_and_target(message, bot, "moderate")
    if not target:
        return
    reason = command.args or "без причины"
    await db.ensure_member(target.id, target.username, target.full_name)
    action, extra = await punishments.apply_verdict(bot, message.chat.id, target.id, "manual", reason, message.from_user.id)
    await message.reply(f"⚠️ Пользователю {await display_mention(target)} вынесено предупреждение.\nПричина: {reason}")


@router.message(Command("mute"))
async def cmd_mute(message: Message, bot: Bot, command: CommandObject):
    target = await _require_permission_and_target(message, bot, "moderate")
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
    await message.reply(f"🔇 {await display_mention(target)} замучен на {minutes} мин.")


@router.message(Command("unmute"))
async def cmd_unmute(message: Message, bot: Bot):
    target = await _require_permission_and_target(message, bot, "moderate")
    if not target:
        return
    await punishments.unmute_user(bot, message.chat.id, target.id)
    await message.reply(f"🔊 С {await display_mention(target)} снят мут.")


@router.message(Command("unwarn"))
async def cmd_unwarn(message: Message, bot: Bot):
    """Снять ОДНО (последнее) предупреждение — точечная отмена ошибочного варна."""
    target = await _require_permission_and_target(message, bot, "moderate")
    if not target:
        return
    removed = await db.remove_last_warning(target.id)
    if not removed:
        await message.reply(f"У {await display_mention(target)} нет активных предупреждений.")
        return
    await db.add_log("unwarn", target.id, message.from_user.id, "")
    remaining = await db.count_active_warnings(target.id)
    await message.reply(f"✅ С {await display_mention(target)} снято последнее предупреждение. Осталось: {remaining}.")


@router.message(Command("clearwarns"))
async def cmd_clearwarns(message: Message, bot: Bot):
    """Снять ВСЕ предупреждения разом (только администраторы — более серьёзное действие, чем /unwarn)."""
    target = await _require_permission_and_target(message, bot, "kick_ban")
    if not target:
        return
    await db.clear_warnings(target.id)
    await db.add_log("clearwarns", target.id, message.from_user.id, "")
    await message.reply(f"✅ Все предупреждения {await display_mention(target)} сняты.")


@router.message(Command("kick"))
async def cmd_kick(message: Message, bot: Bot, command: CommandObject):
    target = await _require_permission_and_target(message, bot, "kick_ban")
    if not target:
        return
    await punishments.kick_user(bot, message.chat.id, target.id, command.args or "", message.from_user.id)
    await message.reply(f"👢 {await display_mention(target)} удалён из чата.")


@router.message(Command("ban"))
async def cmd_ban(message: Message, bot: Bot, command: CommandObject):
    target = await _require_permission_and_target(message, bot, "kick_ban")
    if not target:
        return
    await punishments.ban_user(bot, message.chat.id, target.id, command.args or "", message.from_user.id)
    await message.reply(f"🚫 {await display_mention(target)} забанен.")


@router.message(Command("unban"))
async def cmd_unban(message: Message, bot: Bot):
    target = await _require_permission_and_target(message, bot, "kick_ban")
    if not target:
        return
    await punishments.unban_user(bot, message.chat.id, target.id, message.from_user.id)
    await message.reply(f"✅ {await display_mention(target)} разбанен.")


@router.message(Command("setflood"))
async def cmd_setflood(message: Message, bot: Bot, command: CommandObject):
    if not await _require_permission(message, bot, "manage_settings"):
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
    if not await _require_permission(message, bot, "manage_settings"):
        return
    if not command.args:
        await message.reply("Использование: /setwelcome <текст с {chat_title} и {user_mention}>")
        return
    await db.set_setting("welcome_text", command.args)
    await message.reply("✅ Текст приветствия обновлён.")


async def _require_admin(message: Message, bot: Bot) -> bool:
    """Используется командами управления стоп-словами/доменами/сроком варнов —
    пропускает реальных админов и любую роль с правом manage_settings."""
    return await _require_permission(message, bot, "manage_settings")


@router.message(Command("addword"))
async def cmd_addword(message: Message, bot: Bot, command: CommandObject):
    if not await _require_admin(message, bot):
        return
    if not command.args:
        await message.reply("Использование: /addword <слово или фраза>")
        return
    word = command.args.strip()
    await db.add_banned_word(word)
    await db.add_log("addword", message.from_user.id, message.from_user.id, word)
    await message.reply(f"✅ «{word}» добавлено в стоп-слова.")


@router.message(Command("delword"))
async def cmd_delword(message: Message, bot: Bot, command: CommandObject):
    if not await _require_admin(message, bot):
        return
    if not command.args:
        await message.reply("Использование: /delword <слово или фраза>")
        return
    word = command.args.strip()
    await db.remove_banned_word(word)
    await db.add_log("delword", message.from_user.id, message.from_user.id, word)
    await message.reply(f"✅ «{word}» удалено из стоп-слов.")


@router.message(Command("words"))
async def cmd_words(message: Message, bot: Bot):
    if not await _require_admin(message, bot):
        return
    words = await db.get_banned_words()
    if not words:
        await message.reply("Список стоп-слов пуст.")
        return
    await message.reply("🚫 <b>Стоп-слова</b>\n\n" + "\n".join(f"• {w}" for w in words))


@router.message(Command("adddomain"))
async def cmd_adddomain(message: Message, bot: Bot, command: CommandObject):
    if not await _require_admin(message, bot):
        return
    if not command.args:
        await message.reply("Использование: /adddomain <домен, например spam.ru>")
        return
    domain = command.args.strip()
    await db.add_ad_domain(domain)
    await db.add_log("adddomain", message.from_user.id, message.from_user.id, domain)
    await message.reply(f"✅ «{domain}» добавлен в чёрный список доменов.")


@router.message(Command("deldomain"))
async def cmd_deldomain(message: Message, bot: Bot, command: CommandObject):
    if not await _require_admin(message, bot):
        return
    if not command.args:
        await message.reply("Использование: /deldomain <домен>")
        return
    domain = command.args.strip()
    await db.remove_ad_domain(domain)
    await db.add_log("deldomain", message.from_user.id, message.from_user.id, domain)
    await message.reply(f"✅ «{domain}» удалён из чёрного списка доменов.")


@router.message(Command("domains"))
async def cmd_domains(message: Message, bot: Bot):
    if not await _require_admin(message, bot):
        return
    domains = await db.get_ad_domains()
    if not domains:
        await message.reply("Чёрный список доменов пуст.")
        return
    await message.reply("🚫 <b>Чёрный список доменов</b>\n\n" + "\n".join(f"• {d}" for d in domains))


@router.message(Command("lockdown"))
async def cmd_lockdown(message: Message, bot: Bot):
    """Экстренная ручная блокировка чата (например, при рейде, который бот не поймал сам)."""
    if not _is_group_chat(message.chat.id):
        return
    if not await _is_full_admin(bot, message.chat.id, message.from_user.id):
        await message.reply("⛔ Эта команда доступна только администраторам.")
        return
    if await db.get_bool_setting("lockdown_active"):
        await message.reply("Чат уже заблокирован. Снять блокировку: /unlock")
        return
    engaged = await punishments.engage_lockdown(bot, message.chat.id, f"ручная блокировка от {message.from_user.id}")
    if engaged:
        await message.reply("🔒 Чат заблокирован: участники без прав администратора не могут писать. Снять: /unlock")
    else:
        await message.reply("⚠️ Не удалось заблокировать чат — проверьте, что у бота есть право менять настройки чата.")


@router.message(Command("unlock"))
async def cmd_unlock(message: Message, bot: Bot):
    if not _is_group_chat(message.chat.id):
        return
    if not await _is_full_admin(bot, message.chat.id, message.from_user.id):
        await message.reply("⛔ Эта команда доступна только администраторам.")
        return
    if not await db.get_bool_setting("lockdown_active"):
        await message.reply("Чат сейчас не заблокирован.")
        return
    lifted = await punishments.lift_lockdown(bot, message.chat.id)
    if lifted:
        await message.reply("🔓 Блокировка снята, права чата восстановлены.")
    else:
        await message.reply("⚠️ Не удалось снять блокировку — проверьте права бота и попробуйте ещё раз.")


@router.message(Command("setwarnexpiry"))
async def cmd_setwarnexpiry(message: Message, bot: Bot, command: CommandObject):
    if not await _require_admin(message, bot):
        return
    if not command.args:
        current = await db.get_setting("warn_expiry_days")
        await message.reply(f"Текущий срок действия предупреждений: {current} дней.\nИзменить: /setwarnexpiry <дней>")
        return
    try:
        days = int(command.args.split()[0])
    except ValueError:
        await message.reply("Использование: /setwarnexpiry <дней>")
        return
    await db.set_setting("warn_expiry_days", str(days))
    await message.reply(f"✅ Предупреждения теперь действуют {days} дней.")


@router.message(Command("setname"))
async def cmd_setname(message: Message, command: CommandObject):
    """Позволяет обычному участнику задать имя, которое бот будет использовать
    при упоминаниях в чате (варны, приветствие, левелап и т.д.) вместо имени профиля Telegram.
    Меняется не чаще раза в 2 суток и требует подтверждения кнопкой."""
    if not _is_allowed_chat(message.chat.id):
        return

    cooldown = int(await db.get_setting("nickname_change_cooldown_sec"))
    min_len = int(await db.get_setting("nickname_min_length"))
    max_len = int(await db.get_setting("nickname_max_length"))

    if not command.args:
        current = await db.get_nickname(message.from_user.id)
        current_line = f"Сейчас: «{current}»" if current else "Сейчас используется имя из профиля Telegram."
        days = cooldown // 86400
        await message.reply(
            f"Использование: /setname <новое имя>\n{current_line}\n"
            f"Длина: {min_len}–{max_len} символов. Менять можно не чаще раза в {days} дня(ей)."
        )
        return

    new_name = " ".join(command.args.split())  # схлопнуть лишние пробелы/переносы строк

    if not (min_len <= len(new_name) <= max_len):
        await message.reply(f"Имя должно быть от {min_len} до {max_len} символов.")
        return
    if any(ch in new_name for ch in ("<", ">", "\n", "\t")):
        await message.reply("Имя не должно содержать символы < > и переносы строк.")
        return

    banned = await moderation.check_banned_words(new_name)
    if banned.triggered:
        await message.reply("Это имя недоступно — содержит запрещённое слово.")
        return

    await db.ensure_member(message.from_user.id, message.from_user.username, message.from_user.full_name)
    allowed, remaining = await db.can_change_nickname(message.from_user.id, cooldown)
    if not allowed:
        days = remaining // 86400
        hours = (remaining % 86400) // 3600
        await message.reply(f"⏳ Следующая смена имени будет доступна через {days} дн. {hours} ч.")
        return

    _pending_nicknames[message.from_user.id] = new_name
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Подтвердить", callback_data=f"nickname:confirm:{message.from_user.id}")
    kb.button(text="❌ Отмена", callback_data=f"nickname:cancel:{message.from_user.id}")
    kb.adjust(2)
    await message.reply(
        f"Сменить отображаемое имя на «{html.escape(new_name)}»?\n"
        f"Следующая смена будет доступна только через {cooldown // 86400} дн.",
        reply_markup=kb.as_markup(),
    )


@router.callback_query(F.data.startswith("nickname:"))
async def on_nickname_confirm(callback: CallbackQuery):
    _, action, uid = callback.data.split(":")
    uid = int(uid)
    if callback.from_user.id != uid:
        await callback.answer("Это не ваш запрос на смену имени.", show_alert=True)
        return

    if action == "cancel":
        _pending_nicknames.pop(uid, None)
        await callback.message.edit_text("Отменено. Имя не изменено.")
        await callback.answer()
        return

    new_name = _pending_nicknames.pop(uid, None)
    if new_name is None:
        await callback.answer("Запрос устарел, отправьте /setname ещё раз.", show_alert=True)
        return

    await db.set_nickname(uid, new_name)
    await db.add_log("setname", uid, uid, new_name)
    await callback.message.edit_text(
        f"✅ Готово! Теперь бот будет упоминать вас в чате как «{html.escape(new_name)}»."
    )
    await callback.answer()


@router.message(Command("rank"))
async def cmd_rank(message: Message):
    if not _is_allowed_chat(message.chat.id):
        return
    m = await db.get_member(message.from_user.id)
    if not m:
        await message.reply("Данных пока нет — напишите пару сообщений в чат.")
        return
    rep = await db.get_reputation(message.from_user.id)
    badge = await db.get_equipped_badge(message.from_user.id)
    title_line = f"📈 <b>{message.from_user.full_name}</b>"
    if badge:
        title_line += f" {badge['title'].split(' ', 1)[0]}"  # эмодзи бейджа рядом с именем
    lines = [
        title_line,
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
    await message.reply(f"⭐ {await display_mention(target)} назначен(а) модератором чата.")


@router.message(Command("demote"))
async def cmd_demote(message: Message, bot: Bot):
    target = await _require_admin_and_target(message, bot)
    if not target:
        return
    await db.revoke_role(target.id)
    await db.add_log("demote", target.id, message.from_user.id, "")
    await message.reply(f"➖ {await display_mention(target)} больше не модератор.")


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


@router.message(Command("createrole"))
async def cmd_createrole(message: Message, bot: Bot, command: CommandObject):
    """Создать/обновить кастомную роль с произвольным набором прав (только реальные админы).
    Использование: /createrole <ключ> <название>; <право1,право2,...>"""
    if not _is_group_chat(message.chat.id):
        return
    if not await _is_full_admin(bot, message.chat.id, message.from_user.id):
        await message.reply("⛔ Эта команда доступна только администраторам.")
        return
    if not command.args or ";" not in command.args:
        perms_list = "\n".join(f"• <code>{k}</code> — {v}" for k, v in config.PERMISSIONS.items())
        await message.reply(
            "Использование: /createrole <ключ> <название>; <право1,право2,...>\n"
            "Например: /createrole senior Старший модератор; moderate,kick_ban,view_stats\n\n"
            f"Доступные права:\n{perms_list}"
        )
        return

    head, perms_part = command.args.split(";", 1)
    head_parts = head.strip().split(maxsplit=1)
    if len(head_parts) < 2:
        await message.reply("Нужно указать и ключ, и название роли. См. /createrole без аргументов для примера.")
        return
    role_key, title = head_parts[0].strip(), head_parts[1].strip()
    requested_perms = [p.strip() for p in perms_part.split(",") if p.strip()]
    unknown = [p for p in requested_perms if p not in config.PERMISSIONS]
    if unknown:
        await message.reply(f"Неизвестные права: {', '.join(unknown)}. См. /createrole без аргументов.")
        return

    await db.create_custom_role(role_key, title, ",".join(requested_perms), message.from_user.id)
    await db.add_log("createrole", message.from_user.id, message.from_user.id, f"{role_key}:{','.join(requested_perms)}")
    await message.reply(f"✅ Роль «{title}» (<code>{role_key}</code>) создана с правами: {', '.join(requested_perms) or '—'}")


@router.message(Command("roles"))
async def cmd_roles(message: Message):
    if not _is_allowed_chat(message.chat.id):
        return
    roles = await db.list_custom_roles()
    if not roles:
        await message.reply("Кастомные роли не определены.")
        return
    lines = ["🎭 <b>Роли чата</b>", ""]
    for r in roles:
        lines.append(f"<code>{r['role_key']}</code> — {r['title']}\nПрава: {r['permissions']}")
    await message.reply("\n\n".join(lines))


@router.message(Command("setrole"))
async def cmd_setrole(message: Message, bot: Bot, command: CommandObject):
    """Назначить участнику любую из созданных кастомных ролей (только реальные админы)."""
    if not _is_group_chat(message.chat.id):
        return
    if not await _is_full_admin(bot, message.chat.id, message.from_user.id):
        await message.reply("⛔ Эта команда доступна только администраторам.")
        return
    if not message.reply_to_message:
        await message.reply("ℹ️ Ответьте этой командой на сообщение нужного участника.")
        return
    if not command.args:
        await message.reply("Использование: /setrole <ключ_роли> (ответом на сообщение участника). Список: /roles")
        return

    role_key = command.args.split()[0].strip()
    role = await db.get_custom_role(role_key)
    if role is None:
        await message.reply(f"Роль «{role_key}» не найдена. Сначала создайте её: /createrole. Список: /roles")
        return

    target = message.reply_to_message.from_user
    await db.ensure_member(target.id, target.username, target.full_name)
    await db.grant_role(target.id, role_key, message.from_user.id)
    await db.add_log("setrole", target.id, message.from_user.id, role_key)
    await message.reply(f"✅ {await display_mention(target)} назначен(а) роль «{role['title']}».")


@router.message(Command("removerole"))
async def cmd_removerole(message: Message, bot: Bot):
    if not _is_group_chat(message.chat.id):
        return
    if not await _is_full_admin(bot, message.chat.id, message.from_user.id):
        await message.reply("⛔ Эта команда доступна только администраторам.")
        return
    if not message.reply_to_message:
        await message.reply("ℹ️ Ответьте этой командой на сообщение нужного участника.")
        return
    target = message.reply_to_message.from_user
    await db.revoke_role(target.id)
    await db.add_log("removerole", target.id, message.from_user.id, "")
    await message.reply(f"➖ Роль снята с {await display_mention(target)}.")


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
        await message.reply(f"⏳ Вы уже начисляли репутацию {await display_mention(target)} недавно. Попробуйте позже.")
        return
    await message.reply(f"⭐ {await display_mention(target)} получил(а) +1 к репутации! Теперь: {score}")


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


@router.message(Command("pay"))
async def cmd_pay(message: Message, command: CommandObject):
    """Перевод монет другому участнику (ответом на его сообщение)."""
    if not _is_group_chat(message.chat.id):  # получатель должен быть виден в чате
        return
    if not await db.get_bool_setting("economy_enabled"):
        await message.reply("Экономика в этом чате отключена.")
        return
    if not message.reply_to_message:
        await message.reply("ℹ️ Ответьте командой /pay <сумма> на сообщение получателя.")
        return
    if not command.args:
        await message.reply("Использование: /pay <сумма> (ответом на сообщение получателя)")
        return

    recipient = message.reply_to_message.from_user
    if recipient.id == message.from_user.id:
        await message.reply("Нельзя перевести монеты самому себе 🙂")
        return
    if recipient.is_bot:
        await message.reply("Ботам переводить монеты нет смысла 🙂")
        return

    try:
        amount = int(command.args.split()[0])
    except ValueError:
        await message.reply("Использование: /pay <сумма> (ответом на сообщение получателя)")
        return

    min_amount = int(await db.get_setting("pay_min_amount"))
    max_amount = int(await db.get_setting("pay_max_amount"))
    currency = await db.get_setting("currency_name")
    if amount < min_amount or amount > max_amount:
        await message.reply(f"Сумма перевода должна быть от {min_amount} до {max_amount} {currency}.")
        return

    await db.ensure_member(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await db.ensure_member(recipient.id, recipient.username, recipient.full_name)
    starting = int(await db.get_setting("starting_balance"))
    await db.ensure_wallet(message.from_user.id, starting)
    await db.ensure_wallet(recipient.id, starting)

    sender_balance = await db.get_balance(message.from_user.id)
    if sender_balance < amount:
        await message.reply(f"Недостаточно средств. Ваш баланс: {sender_balance} {currency}.")
        return

    await db.add_balance(message.from_user.id, -amount, f"pay_to:{recipient.id}")
    await db.add_balance(recipient.id, amount, f"pay_from:{message.from_user.id}")
    await db.add_log("pay", recipient.id, message.from_user.id, f"amount={amount}")

    await message.reply(
        f"💸 {await display_mention(message.from_user)} перевёл(а) {amount} {currency} "
        f"пользователю {await display_mention(recipient)}."
    )


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
    await message.reply(f"✅ {await display_mention(target)} получил(а) {amount} {currency}. Баланс: {new_balance}")


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


async def _check_minigame_bet(message: Message, bet_str: str, min_key: str, max_key: str):
    """Общая проверка для всех мини-игр: включена ли экономика, валидна ли ставка,
    хватает ли средств. Возвращает (bet, currency) при успехе, иначе (None, None)
    — сообщение об ошибке уже отправлено пользователю."""
    if not await db.get_bool_setting("economy_enabled") or not await db.get_bool_setting("minigames_enabled"):
        await message.reply("Мини-игры сейчас отключены.")
        return None, None
    try:
        bet = int(bet_str)
    except (ValueError, TypeError):
        await message.reply("Ставка должна быть целым числом.")
        return None, None

    min_bet = int(await db.get_setting(min_key))
    max_bet = int(await db.get_setting(max_key))
    currency = await db.get_setting("currency_name")

    if bet < min_bet or bet > max_bet:
        await message.reply(f"Ставка должна быть от {min_bet} до {max_bet} {currency}.")
        return None, None

    await db.ensure_member(message.from_user.id, message.from_user.username, message.from_user.full_name)
    starting = int(await db.get_setting("starting_balance"))
    await db.ensure_wallet(message.from_user.id, starting)
    balance = await db.get_balance(message.from_user.id)
    if balance < bet:
        await message.reply(f"Недостаточно средств. Ваш баланс: {balance} {currency}.")
        return None, None

    return bet, currency


@router.message(Command("dice"))
async def cmd_dice(message: Message, command: CommandObject):
    """Мини-игра: ставка на кубик 1-6. Выпало 4-6 — выигрыш x2, иначе ставка сгорает."""
    if not _is_allowed_chat(message.chat.id):
        return
    if not command.args:
        await message.reply("Использование: /dice <ставка>")
        return

    bet, currency = await _check_minigame_bet(message, command.args.split()[0], "dice_min_bet", "dice_max_bet")
    if bet is None:
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


@router.message(Command("coinflip"))
async def cmd_coinflip(message: Message, command: CommandObject):
    """Мини-игра: орёл/решка. Угадал сторону — выигрыш x2."""
    if not _is_allowed_chat(message.chat.id):
        return
    if not command.args or len(command.args.split()) < 2:
        await message.reply("Использование: /coinflip <ставка> <орёл|решка>")
        return

    bet_str, side_raw = command.args.split()[0], command.args.split()[1].lower()
    side_map = {"орел": "орёл", "орёл": "орёл", "решка": "решка", "heads": "орёл", "tails": "решка"}
    side = side_map.get(side_raw)
    if side is None:
        await message.reply("Сторона должна быть «орёл» или «решка». Использование: /coinflip <ставка> <орёл|решка>")
        return

    bet, currency = await _check_minigame_bet(message, bet_str, "coinflip_min_bet", "coinflip_max_bet")
    if bet is None:
        return

    result = random.choice(["орёл", "решка"])
    suspense = await message.reply("🪙 Подбрасываем монетку...")
    await asyncio.sleep(1.5)

    if result == side:
        new_balance = await db.add_balance(message.from_user.id, bet, "coinflip_win")
        await suspense.edit_text(f"🪙 Выпал(а) «{result}»! Вы угадали и выиграли {bet} {currency}.\nБаланс: {new_balance}")
    else:
        new_balance = await db.add_balance(message.from_user.id, -bet, "coinflip_loss")
        await suspense.edit_text(f"🪙 Выпал(а) «{result}». Вы проиграли {bet} {currency}.\nБаланс: {new_balance}")


SLOT_SYMBOLS = ["🍒", "🍋", "⭐", "💎", "7️⃣"]
SLOT_TRIPLE_MULTIPLIER = {"💎": 8, "7️⃣": 6, "⭐": 5}
SLOT_TRIPLE_DEFAULT_MULTIPLIER = 3


@router.message(Command("slots"))
async def cmd_slots(message: Message, command: CommandObject):
    """Мини-игра: слот-машина. 3 одинаковых символа — крупный выигрыш, 2 одинаковых — возврат ставки."""
    if not _is_allowed_chat(message.chat.id):
        return
    if not command.args:
        await message.reply("Использование: /slots <ставка>")
        return

    bet, currency = await _check_minigame_bet(message, command.args.split()[0], "slots_min_bet", "slots_max_bet")
    if bet is None:
        return

    suspense = await message.reply("🎰 [ ❓ | ❓ | ❓ ]\nКрутим барабан...")
    await asyncio.sleep(1.5)

    reels = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
    reel_text = f"[ {reels[0]} | {reels[1]} | {reels[2]} ]"

    if reels[0] == reels[1] == reels[2]:
        multiplier = SLOT_TRIPLE_MULTIPLIER.get(reels[0], SLOT_TRIPLE_DEFAULT_MULTIPLIER)
        winnings = bet * multiplier
        new_balance = await db.add_balance(message.from_user.id, winnings, "slots_win")
        await suspense.edit_text(
            f"🎰 {reel_text}\n💥 Джекпот! Три одинаковых — выигрыш x{multiplier}: {winnings} {currency}.\nБаланс: {new_balance}"
        )
    elif reels[0] == reels[1] or reels[1] == reels[2] or reels[0] == reels[2]:
        winnings = bet * 3 // 2  # небольшой выигрыш x1.5 при двух совпадениях
        new_balance = await db.add_balance(message.from_user.id, winnings - bet, "slots_small_win")
        await suspense.edit_text(f"🎰 {reel_text}\nДва совпадения — небольшой выигрыш: {winnings} {currency}.\nБаланс: {new_balance}")
    else:
        new_balance = await db.add_balance(message.from_user.id, -bet, "slots_loss")
        await suspense.edit_text(f"🎰 {reel_text}\nНичего не совпало — ставка сгорела.\nБаланс: {new_balance}")


@router.message(Command("duel"))
async def cmd_duel(message: Message, command: CommandObject):
    """Дуэль на ставку между двумя игроками (ответом на сообщение соперника).
    Требует подтверждения от соперника кнопкой — деньги списываются только после его согласия."""
    global _duel_counter

    if not _is_group_chat(message.chat.id):  # дуэль — только в группе, второй игрок должен быть виден в чате
        return
    if not await db.get_bool_setting("economy_enabled") or not await db.get_bool_setting("minigames_enabled"):
        await message.reply("Мини-игры сейчас отключены.")
        return
    if not message.reply_to_message:
        await message.reply("ℹ️ Ответьте командой /duel <ставка> на сообщение соперника, чтобы бросить ему вызов.")
        return
    if not command.args:
        await message.reply("Использование: /duel <ставка> (ответом на сообщение соперника)")
        return

    opponent = message.reply_to_message.from_user
    if opponent.id == message.from_user.id:
        await message.reply("Нельзя вызвать на дуэль самого себя 🙂")
        return
    if opponent.is_bot:
        await message.reply("Боты на дуэли не выходят 🙂")
        return

    try:
        bet = int(command.args.split()[0])
    except ValueError:
        await message.reply("Использование: /duel <ставка> (ответом на сообщение соперника)")
        return

    min_bet = int(await db.get_setting("duel_min_bet"))
    max_bet = int(await db.get_setting("duel_max_bet"))
    currency = await db.get_setting("currency_name")
    if bet < min_bet or bet > max_bet:
        await message.reply(f"Ставка должна быть от {min_bet} до {max_bet} {currency}.")
        return

    await db.ensure_member(message.from_user.id, message.from_user.username, message.from_user.full_name)
    await db.ensure_member(opponent.id, opponent.username, opponent.full_name)
    starting = int(await db.get_setting("starting_balance"))
    await db.ensure_wallet(message.from_user.id, starting)
    await db.ensure_wallet(opponent.id, starting)

    initiator_balance = await db.get_balance(message.from_user.id)
    if initiator_balance < bet:
        await message.reply(f"Недостаточно средств для такой ставки. Ваш баланс: {initiator_balance} {currency}.")
        return

    _duel_counter += 1
    duel_id = _duel_counter
    _pending_duels[duel_id] = {
        "initiator_id": message.from_user.id,
        "target_id": opponent.id,
        "bet": bet,
        "chat_id": message.chat.id,
    }

    timeout = int(await db.get_setting("duel_timeout_sec"))
    sent = await message.reply(
        f"⚔️ {await display_mention(message.from_user)} вызывает {await display_mention(opponent)} на дуэль "
        f"на ставку {bet} {currency}!\n{await display_mention(opponent)}, принимаете вызов?",
        reply_markup=keyboards.duel_challenge_keyboard(duel_id),
    )
    asyncio.create_task(_duel_timeout(sent, duel_id, timeout))


async def _duel_timeout(message: Message, duel_id: int, timeout: int):
    await asyncio.sleep(timeout)
    if duel_id in _pending_duels:
        del _pending_duels[duel_id]
        try:
            await message.edit_text(message.text + "\n\n⌛ Время на ответ истекло, вызов отменён.", reply_markup=None)
        except Exception:
            pass


@router.callback_query(F.data.startswith("duel:"))
async def on_duel_response(callback: CallbackQuery, bot: Bot):
    _, action, duel_id_str = callback.data.split(":")
    duel_id = int(duel_id_str)
    duel = _pending_duels.get(duel_id)

    if duel is None:
        await callback.answer("Этот вызов уже неактуален.", show_alert=True)
        return

    if callback.from_user.id != duel["target_id"]:
        await callback.answer("Этот вызов адресован не вам.", show_alert=True)
        return

    currency = await db.get_setting("currency_name")

    if action == "decline":
        del _pending_duels[duel_id]
        await callback.message.edit_text(callback.message.text + "\n\n❌ Вызов отклонён.", reply_markup=None)
        await callback.answer()
        return

    # action == "accept"
    bet = duel["bet"]
    target_balance = await db.get_balance(duel["target_id"])
    if target_balance < bet:
        await callback.answer(f"Недостаточно средств для этой ставки (у вас {target_balance} {currency}).", show_alert=True)
        return

    initiator_balance = await db.get_balance(duel["initiator_id"])
    if initiator_balance < bet:
        del _pending_duels[duel_id]
        await callback.message.edit_text(
            callback.message.text + "\n\n❌ Вызов отменён: у инициатора больше не хватает средств.", reply_markup=None
        )
        await callback.answer()
        return

    del _pending_duels[duel_id]

    winner_id = random.choice([duel["initiator_id"], duel["target_id"]])
    loser_id = duel["target_id"] if winner_id == duel["initiator_id"] else duel["initiator_id"]

    await db.add_balance(winner_id, bet, f"duel_win:{duel_id}")
    await db.add_balance(loser_id, -bet, f"duel_loss:{duel_id}")

    # Реальные User-объекты обеих сторон нужны для корректного тега (учитывая /setname) —
    # у нас гарантированно есть только callback.from_user (это target), для второй стороны
    # запрашиваем актуальные данные участника через Bot API.
    async def _resolve_user(user_id: int):
        if user_id == callback.from_user.id:
            return callback.from_user
        try:
            member = await bot.get_chat_member(duel["chat_id"], user_id)
            return member.user
        except Exception:
            return None

    winner_user = await _resolve_user(winner_id)
    loser_user = await _resolve_user(loser_id)

    winner_mention = await display_mention(winner_user) if winner_user else f'<a href="tg://user?id={winner_id}">Победитель</a>'
    loser_mention = await display_mention(loser_user) if loser_user else f'<a href="tg://user?id={loser_id}">Соперник</a>'

    await db.add_log("duel_result", winner_id, loser_id, f"duel_id={duel_id} bet={bet}")

    await callback.message.edit_text(
        callback.message.text + f"\n\n⚔️ Дуэль состоялась! Победитель: {winner_mention} (+{bet} {currency}).\n"
        f"Проигравший: {loser_mention} (-{bet} {currency}).",
        reply_markup=None,
    )
    await callback.answer("Дуэль завершена!")


@router.message(Command("shop"))
async def cmd_shop(message: Message):
    if not _is_allowed_chat(message.chat.id):
        return
    if not await db.get_bool_setting("economy_enabled"):
        await message.reply("Экономика в этом чате отключена.")
        return
    items = await db.get_shop_items()
    if not items:
        await message.reply("Магазин пока пуст.")
        return
    currency = await db.get_setting("currency_name")
    lines = ["🛒 <b>Магазин</b>", ""]
    for item in items:
        lines.append(f"<code>{item['key']}</code> — {item['title']} — {item['price']} {currency}\n{item['description']}")
    lines.append("\nКупить: /buy <код_товара>")
    await message.reply("\n\n".join(lines))


@router.message(Command("buy"))
async def cmd_buy(message: Message, command: CommandObject):
    if not _is_allowed_chat(message.chat.id):
        return
    if not await db.get_bool_setting("economy_enabled"):
        await message.reply("Экономика в этом чате отключена.")
        return
    if not command.args:
        await message.reply("Использование: /buy <код_товара> (см. /shop)")
        return

    item_key = command.args.split()[0].strip()
    await db.ensure_member(message.from_user.id, message.from_user.username, message.from_user.full_name)
    starting = int(await db.get_setting("starting_balance"))
    await db.ensure_wallet(message.from_user.id, starting)

    success, error = await db.buy_item(message.from_user.id, item_key)
    if not success:
        await message.reply(f"❌ {error}")
        return

    item = await db.get_shop_item(item_key)
    await db.add_log("shop_buy", message.from_user.id, None, item_key)
    await message.reply(
        f"✅ Куплено: {item['title']}!\nПрименить его как активный бейдж: /equip {item_key}"
    )


@router.message(Command("inventory"))
async def cmd_inventory(message: Message):
    if not _is_allowed_chat(message.chat.id):
        return
    rows = await db.get_inventory(message.from_user.id)
    if not rows:
        await message.reply("🎒 Ваш инвентарь пуст. Загляните в /shop.")
        return
    lines = ["🎒 <b>Ваш инвентарь</b>", ""]
    for r in rows:
        mark = " (экипирован)" if r["equipped"] else ""
        lines.append(f"• {r['title']} × {r['quantity']}{mark}")
    lines.append("\nЭкипировать: /equip <код_товара>")
    await message.reply("\n".join(lines))


@router.message(Command("equip"))
async def cmd_equip(message: Message, command: CommandObject):
    if not _is_allowed_chat(message.chat.id):
        return
    if not command.args:
        await message.reply("Использование: /equip <код_товара> (см. /inventory)")
        return
    item_key = command.args.split()[0].strip()
    ok = await db.equip_item(message.from_user.id, item_key)
    if not ok:
        await message.reply("У вас нет такого предмета. Проверьте /inventory.")
        return
    item = await db.get_shop_item(item_key)
    await message.reply(f"✅ Бейдж «{item['title']}» теперь отображается в /rank.")


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
            await bot.send_message(chat_id, f"🎉 {await display_mention(user)} достиг {new_level} уровня!{bonus_text}")
        except Exception:
            pass

    for ach in await db.check_and_award_achievements(user.id):
        try:
            await bot.send_message(
                chat_id, f"🏆 {await display_mention(user)} получил(а) достижение «{ach['title']}»!\n{ach['description']}"
            )
        except Exception:
            pass


# Типы сообщений, которые реально может отправить участник и которые должны попадать под
# антифлуд/модерацию. Раньше хендлер слушал только F.text | F.caption, из-за чего спам
# стикерами, фото и голосовыми без подписи полностью обходил антифлуд — это было дырой.
# Служебные сообщения (вход/выход участников и т.п.) сюда намеренно не входят.
MODERATABLE_CONTENT_TYPES = {
    ContentType.TEXT, ContentType.PHOTO, ContentType.VIDEO, ContentType.ANIMATION,
    ContentType.STICKER, ContentType.VOICE, ContentType.VIDEO_NOTE, ContentType.DOCUMENT,
    ContentType.AUDIO, ContentType.CONTACT, ContentType.LOCATION, ContentType.VENUE, ContentType.GAME,
}


@router.message(F.content_type.in_(MODERATABLE_CONTENT_TYPES))
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

        mention = await display_mention(message.from_user)
        if action == "warn":
            notice = f"⚠️ {mention}, предупреждение: {final_verdict.reason}"
        elif action == "mute":
            notice = f"🔇 {mention} замучен ({extra // 60} мин.). Причина: {final_verdict.reason}"
        else:  # ban
            notice = f"🚫 {mention} забанен за повторные нарушения."

        try:
            sent = await bot.send_message(message.chat.id, notice)
            await asyncio.sleep(8)
            await sent.delete()
        except Exception:
            pass
        return

    await _process_engagement(bot, message.chat.id, message.from_user)


# ---------------------------------------------------------------------------
# Восстановление состояния после рестарта процесса
# ---------------------------------------------------------------------------

async def _reconcile_after_restart(bot: Bot):
    """При каждом старте бота (в т.ч. после деплоя/краша на Railway) восстанавливает
    таймеры автоснятия мута и капчи, которые хранились только в памяти предыдущего
    процесса и были бы потеряны. Источник истины — БД (muted_until / deadline)."""
    now = int(time.time())

    pending_list = await db.list_all_pending_captcha()
    for pending in pending_list:
        remaining = pending["deadline"] - now
        if remaining <= 0:
            await db.remove_pending_captcha(pending["user_id"])
            try:
                await bot.delete_message(config.ALLOWED_CHAT_ID, pending["join_message_id"])
            except Exception:
                pass
            try:
                await bot.ban_chat_member(config.ALLOWED_CHAT_ID, pending["user_id"])
                await bot.unban_chat_member(config.ALLOWED_CHAT_ID, pending["user_id"], only_if_banned=True)
            except Exception as e:
                logger.warning(f"Reconcile: не удалось кикнуть просроченную капчу {pending['user_id']}: {e}")
            await db.add_log("captcha_timeout_kick", pending["user_id"], None, "reconciled_after_restart")
        else:
            asyncio.create_task(
                _captcha_timeout_kick(bot, config.ALLOWED_CHAT_ID, pending["user_id"], pending["join_message_id"], remaining)
            )

    muted_list = await db.list_all_muted()
    for member in muted_list:
        remaining = member["muted_until"] - now
        if remaining <= 0:
            await punishments.unmute_user(bot, config.ALLOWED_CHAT_ID, member["user_id"], automatic=True)
        else:
            asyncio.create_task(punishments._auto_unmute(bot, config.ALLOWED_CHAT_ID, member["user_id"], remaining))

    if pending_list or muted_list:
        logger.info(
            "Восстановлено после рестарта: %d капч(и) в ожидании, %d активных мутов.",
            len(pending_list), len(muted_list),
        )


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
        await _reconcile_after_restart(bot)
        await dp.start_polling(bot)
    finally:
        await db.close_db()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
