"""
Клавиатуры кнопочной админ-панели (/settings).
Каждая настройка отображает текущее значение прямо в кнопке (принцип "Предсказуемость", п.0.3 ТЗ).
"""
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db


def main_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🚫 Модерация", callback_data="panel:moderation")
    b.button(text="📜 Логи", callback_data="panel:logs:0")
    b.button(text="👥 Пользователи", callback_data="panel:users")
    b.button(text="📨 Жалобы", callback_data="panel:reports:0")
    b.button(text="🎭 Роли", callback_data="panel:roles")
    b.button(text="⭐ Репутация", callback_data="panel:reputation")
    b.button(text="💰 Экономика", callback_data="panel:economy")
    b.button(text="🛒 Магазин", callback_data="panel:shop")
    b.button(text="🏆 Достижения", callback_data="panel:achievements")
    b.button(text="📊 Статистика", callback_data="panel:stats")
    b.button(text="🚨 Антирейд", callback_data="panel:antiraid")
    b.button(text="🎉 Розыгрыши", callback_data="panel:giveaways")
    b.button(text="⚙️ Настройки", callback_data="panel:settings")
    b.button(text="❌ Закрыть", callback_data="panel:close")
    b.adjust(2, 2, 2, 2, 2, 2, 2)
    return b.as_markup()


async def moderation_menu() -> InlineKeyboardMarkup:
    settings = await db.get_all_settings()
    b = InlineKeyboardBuilder()

    def onoff(key):
        return "✅ Вкл" if settings.get(key) == "1" else "⛔ Выкл"

    b.button(text=f"Антифлуд: {onoff('antiflood_enabled')}", callback_data="panel:mod:toggle:antiflood_enabled")
    b.button(text=f"Антиспам: {onoff('antispam_enabled')}", callback_data="panel:mod:toggle:antispam_enabled")
    b.button(text=f"Антиреклама: {onoff('antiad_enabled')}", callback_data="panel:mod:toggle:antiad_enabled")
    b.button(text=f"Антикапс: {onoff('anticaps_enabled')}", callback_data="panel:mod:toggle:anticaps_enabled")
    b.button(text=f"Антиупоминания: {onoff('antimention_enabled')}", callback_data="panel:mod:toggle:antimention_enabled")
    b.button(text=f"Антиповторы: {onoff('antirepeat_enabled')}", callback_data="panel:mod:toggle:antirepeat_enabled")
    b.button(text=f"Капча новичкам: {onoff('captcha_enabled')}", callback_data="panel:mod:toggle:captcha_enabled")
    b.button(
        text=f"Порог флуда: {settings.get('antiflood_limit')} msg / {settings.get('antiflood_window_sec')}s",
        callback_data="panel:mod:info:antiflood",
    )
    b.button(text=f"Срок действия варнов: {settings.get('warn_expiry_days')} дн.", callback_data="panel:mod:info:antiflood")
    b.button(text="🔙 Назад", callback_data="panel:main")
    b.adjust(1)
    return b.as_markup()


def users_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔇 Замученные", callback_data="panel:users:muted:0")
    b.button(text="🚫 Забаненные", callback_data="panel:users:banned:0")
    b.button(text="🔙 Назад", callback_data="panel:main")
    b.adjust(2, 1)
    return b.as_markup()


def back_only_menu(target: str = "panel:main") -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Назад", callback_data=target)
    return b.as_markup()


def roles_menu() -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="🔙 Назад", callback_data="panel:main")
    return b.as_markup()


async def antiraid_menu() -> InlineKeyboardMarkup:
    settings = await db.get_all_settings()
    b = InlineKeyboardBuilder()

    def onoff(key):
        return "✅ Вкл" if settings.get(key) == "1" else "⛔ Выкл"

    b.button(text=f"Детекция рейда: {onoff('raid_detection_enabled')}", callback_data="panel:antiraid:toggle:raid_detection_enabled")
    b.button(text=f"Автоблокировка: {onoff('raid_auto_lockdown_enabled')}", callback_data="panel:antiraid:toggle:raid_auto_lockdown_enabled")
    b.button(
        text=f"Порог: {settings.get('raid_join_threshold')} вход. / {settings.get('raid_window_sec')}с",
        callback_data="panel:antiraid:info",
    )
    if settings.get("lockdown_active") == "1":
        b.button(text="🔓 Снять блокировку сейчас", callback_data="panel:antiraid:unlock")
    else:
        b.button(text="🔒 Заблокировать чат сейчас", callback_data="panel:antiraid:lockdown")
    b.button(text="🔙 Назад", callback_data="panel:main")
    b.adjust(1)
    return b.as_markup()


async def economy_menu() -> InlineKeyboardMarkup:
    settings = await db.get_all_settings()
    b = InlineKeyboardBuilder()

    def onoff(key):
        return "✅ Вкл" if settings.get(key) == "1" else "⛔ Выкл"

    b.button(text=f"Экономика: {onoff('economy_enabled')}", callback_data="panel:economy:toggle:economy_enabled")
    b.button(text=f"Мини-игры: {onoff('minigames_enabled')}", callback_data="panel:economy:toggle:minigames_enabled")
    b.button(
        text=f"Награда/сообщение: {settings.get('message_reward')} {settings.get('currency_name')}",
        callback_data="panel:economy:info",
    )
    b.button(
        text=f"Ежедневный бонус: {settings.get('daily_bonus_amount')} {settings.get('currency_name')}",
        callback_data="panel:economy:info",
    )
    b.button(text="🔙 Назад", callback_data="panel:main")
    b.adjust(1)
    return b.as_markup()


def paginated_user_list(rows, kind: str, offset: int, total: int, limit: int = 8) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for row in rows:
        name = row["username"] or row["full_name"] or str(row["user_id"])
        action = "unmute" if kind == "muted" else "unban"
        label = f"↩️ Снять с @{name}" if kind == "muted" else f"↩️ Разбанить @{name}"
        b.button(text=label, callback_data=f"panel:{action}:{row['user_id']}")
    b.adjust(1)

    nav_row = []
    if offset > 0:
        nav_row.append(
            InlineKeyboardButton(text="◀️ Пред.", callback_data=f"panel:users:{kind}:{max(0, offset - limit)}")
        )
    if offset + limit < total:
        nav_row.append(
            InlineKeyboardButton(text="След. ▶️", callback_data=f"panel:users:{kind}:{offset + limit}")
        )
    if nav_row:
        b.row(*nav_row)

    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data="panel:users"))
    return b.as_markup()


def logs_list(offset: int, total: int, limit: int = 10) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    nav_row = []
    if offset > 0:
        nav_row.append(InlineKeyboardButton(text="◀️ Пред.", callback_data=f"panel:logs:{max(0, offset - limit)}"))
    if offset + limit < total:
        nav_row.append(InlineKeyboardButton(text="След. ▶️", callback_data=f"panel:logs:{offset + limit}"))
    if nav_row:
        b.row(*nav_row)
    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data="panel:main"))
    return b.as_markup()


async def settings_menu() -> InlineKeyboardMarkup:
    settings = await db.get_all_settings()
    onoff = "✅ Вкл" if settings.get("welcome_enabled") == "1" else "⛔ Выкл"
    b = InlineKeyboardBuilder()
    b.button(text=f"Приветствие новичков: {onoff}", callback_data="panel:settings:toggle:welcome_enabled")
    b.button(text="✏️ Изменить текст приветствия", callback_data="panel:settings:edit_welcome")
    b.button(text="🔙 Назад", callback_data="panel:main")
    b.adjust(1)
    return b.as_markup()


def confirm_keyboard(action: str, target: str) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="✅ Да", callback_data=f"panel:confirm:{action}:{target}")
    b.button(text="❌ Отмена", callback_data="panel:main")
    b.adjust(2)
    return b.as_markup()


def duel_challenge_keyboard(duel_id: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text="⚔️ Принять вызов", callback_data=f"duel:accept:{duel_id}")
    b.button(text="❌ Отклонить", callback_data=f"duel:decline:{duel_id}")
    b.adjust(2)
    return b.as_markup()


def giveaway_keyboard(giveaway_id: int, participants_count: int) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    b.button(text=f"🎉 Участвовать ({participants_count})", callback_data=f"giveaway:join:{giveaway_id}")
    return b.as_markup()


def giveaways_panel_list(rows) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for g in rows:
        b.button(text=f"🏁 Завершить #{g['id']}", callback_data=f"panel:giveaway_end:{g['id']}")
    b.adjust(1)
    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data="panel:main"))
    return b.as_markup()


def report_action_keyboard(report_id: int) -> InlineKeyboardMarkup:
    """Кнопки прямо под уведомлением о жалобе в чате — для быстрой реакции админа/модератора."""
    b = InlineKeyboardBuilder()
    b.button(text="🔇 Мут 30м", callback_data=f"report:mute:{report_id}")
    b.button(text="🚫 Бан", callback_data=f"report:ban:{report_id}")
    b.button(text="✅ Отклонить", callback_data=f"report:dismiss:{report_id}")
    b.adjust(3)
    return b.as_markup()


def reports_list(rows, offset: int, total: int, limit: int = 8) -> InlineKeyboardMarkup:
    b = InlineKeyboardBuilder()
    for r in rows:
        b.button(text=f"Жалоба #{r['id']}", callback_data=f"panel:report_view:{r['id']}")
    b.adjust(2)

    nav_row = []
    if offset > 0:
        nav_row.append(InlineKeyboardButton(text="◀️ Пред.", callback_data=f"panel:reports:{max(0, offset - limit)}"))
    if offset + limit < total:
        nav_row.append(InlineKeyboardButton(text="След. ▶️", callback_data=f"panel:reports:{offset + limit}"))
    if nav_row:
        b.row(*nav_row)

    b.row(InlineKeyboardButton(text="🔙 Назад", callback_data="panel:main"))
    return b.as_markup()
