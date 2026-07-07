"""
Обработчики callback-ов кнопочной админ-панели.

Правило доступа (раздел 3.4 ТЗ): нажатие кнопки не-администратором ->
всплывающее уведомление "Только для администраторов", экран не меняется.
"""
import time

from aiogram import Router, F, Bot
from aiogram.types import CallbackQuery

import db
import config
import keyboards
import punishments

router = Router(name="panel")


async def _is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("creator", "administrator")
    except Exception:
        return False


@router.callback_query(F.data.startswith("panel:"))
async def panel_router(callback: CallbackQuery, bot: Bot):
    if not await _is_admin(bot, callback.message.chat.id, callback.from_user.id):
        await callback.answer("⛔ Только для администраторов", show_alert=True)
        return

    parts = callback.data.split(":")
    section = parts[1]

    if section == "main":
        await callback.message.edit_text(
            "🛠 <b>Админ-панель @RewchikChat</b>\nВыберите раздел:", reply_markup=keyboards.main_menu()
        )

    elif section == "close":
        await callback.message.delete()

    elif section == "moderation":
        await callback.message.edit_text(
            "🚫 <b>Модерация</b>\nВключение/выключение анти-модулей чата:",
            reply_markup=await keyboards.moderation_menu(),
        )

    elif section == "mod" and parts[2] == "toggle":
        key = parts[3]
        current = await db.get_bool_setting(key)
        await db.set_setting(key, "0" if current else "1")
        await db.add_log("settings_change", callback.from_user.id, callback.from_user.id, f"{key} -> {not current}")
        await callback.message.edit_text(
            "🚫 <b>Модерация</b>\nВключение/выключение анти-модулей чата:",
            reply_markup=await keyboards.moderation_menu(),
        )
        await callback.answer("Настройка обновлена ✅")

    elif section == "mod" and parts[2] == "info":
        await callback.answer(
            "Изменить пороги можно командой /setflood <лимит> <окно_сек> в чате (ответом на любое сообщение).",
            show_alert=True,
        )

    elif section == "users":
        if len(parts) == 2:
            await callback.message.edit_text("👥 <b>Пользователи</b>", reply_markup=keyboards.users_menu())
        else:
            kind, offset = parts[2], int(parts[3])
            await _show_user_list(callback, kind, offset)

    elif section == "unmute":
        user_id = int(parts[2])
        await punishments.unmute_user(bot, callback.message.chat.id, user_id)
        await callback.answer("Мут снят ✅")
        await _show_user_list(callback, "muted", 0)

    elif section == "unban":
        user_id = int(parts[2])
        await punishments.unban_user(bot, callback.message.chat.id, user_id, callback.from_user.id)
        await callback.answer("Разбанен ✅")
        await _show_user_list(callback, "banned", 0)

    elif section == "logs":
        offset = int(parts[2])
        await _show_logs(callback, offset)

    elif section == "roles":
        rows = await db.list_roles(config.ROLE_MODERATOR)
        if not rows:
            text = "🎭 <b>Роли</b>\n\nНазначенных модераторов пока нет."
        else:
            lines = ["🎭 <b>Модераторы чата</b>", ""]
            for r in rows:
                m = await db.get_member(r["user_id"])
                name = (m["username"] or m["full_name"]) if m else str(r["user_id"])
                lines.append(f"• @{name}")
            text = "\n".join(lines)
        text += "\n\nНазначить: ответьте на сообщение участника командой /promote в чате.\nСнять: /demote."
        await callback.message.edit_text(text, reply_markup=keyboards.roles_menu())

    elif section == "reputation":
        rows = await db.top_reputation(10)
        if not rows:
            text = "⭐ <b>Репутация</b>\n\nПока никто не получил репутацию."
        else:
            lines = ["⭐ <b>Топ по репутации</b>", ""]
            for i, r in enumerate(rows, 1):
                name = r["username"] or r["full_name"] or r["user_id"]
                lines.append(f"{i}. @{name} — {r['score']}")
            text = "\n".join(lines)
        text += "\n\nНачислить: ответить на сообщение участника командой /rep в чате."
        await callback.message.edit_text(text, reply_markup=keyboards.back_only_menu())

    elif section == "stats":
        o = await db.chat_overview()
        text = (
            "📊 <b>Обзор чата @RewchikChat</b>\n\n"
            f"👥 Участников в базе: {o['total_members']}\n"
            f"🔇 Сейчас замучено: {o['muted']}\n"
            f"🚫 Забанено: {o['banned']}\n"
            f"💬 Всего сообщений учтено: {o['total_messages']}\n"
            f"⚠️ Нарушений за 24 часа: {o['violations_24h']}"
        )
        await callback.message.edit_text(text, reply_markup=keyboards.back_only_menu())

    elif section == "economy":
        if len(parts) == 2:
            await callback.message.edit_text(
                "💰 <b>Экономика и мини-игры</b>", reply_markup=await keyboards.economy_menu()
            )
        elif parts[2] == "toggle":
            key = parts[3]
            current = await db.get_bool_setting(key)
            await db.set_setting(key, "0" if current else "1")
            await db.add_log("settings_change", callback.from_user.id, callback.from_user.id, f"{key} -> {not current}")
            await callback.message.edit_text(
                "💰 <b>Экономика и мини-игры</b>", reply_markup=await keyboards.economy_menu()
            )
            await callback.answer("Обновлено ✅")
        elif parts[2] == "info":
            await callback.answer(
                "Изменить суммы можно прямым редактированием таблицы settings в БД "
                "(message_reward, daily_bonus_amount, levelup_bonus_amount, dice_min_bet, dice_max_bet).",
                show_alert=True,
            )

    elif section == "achievements":
        all_ach = await db.get_all_achievements()
        lines = ["🏆 <b>Достижения чата</b>", ""]
        for ach in all_ach:
            lines.append(f"• {ach['title']} — {ach['description']}")
        await callback.message.edit_text("\n".join(lines), reply_markup=keyboards.back_only_menu())

    elif section == "shop":
        items = await db.get_shop_items(active_only=False)
        currency = await db.get_setting("currency_name")
        lines = ["🛒 <b>Товары магазина</b>", ""]
        for item in items:
            status = "✅" if item["active"] else "⛔ (скрыт)"
            lines.append(f"{status} <code>{item['key']}</code> — {item['title']} — {item['price']} {currency}")
        lines.append("\nУчастники покупают через /buy <код> в чате. Список кодов см. выше.")
        await callback.message.edit_text("\n".join(lines), reply_markup=keyboards.back_only_menu())

    elif section == "settings":
        if len(parts) == 2:
            await callback.message.edit_text("⚙️ <b>Настройки</b>", reply_markup=await keyboards.settings_menu())
        elif parts[2] == "toggle":
            key = parts[3]
            current = await db.get_bool_setting(key)
            await db.set_setting(key, "0" if current else "1")
            await callback.message.edit_text("⚙️ <b>Настройки</b>", reply_markup=await keyboards.settings_menu())
            await callback.answer("Обновлено ✅")
        elif parts[2] == "edit_welcome":
            await callback.answer(
                "Отправьте новый текст приветствия командой /setwelcome <текст> "
                "(доступны {chat_title} и {user_mention}).",
                show_alert=True,
            )

    await callback.answer()


async def _show_user_list(callback: CallbackQuery, kind: str, offset: int):
    if kind == "muted":
        rows = await db.list_muted(offset=offset)
        total = await db.count_muted()
        title = "🔇 Замученные участники"
    else:
        rows = await db.list_banned(offset=offset)
        total = await db.count_banned()
        title = "🚫 Забаненные участники"

    if not rows:
        text = f"{title}\n\nСписок пуст."
    else:
        lines = [title, ""]
        for r in rows:
            name = r["username"] or r["full_name"] or r["user_id"]
            if kind == "muted":
                remaining = max(0, r["muted_until"] - int(time.time()))
                lines.append(f"• @{name} — ещё {remaining // 60} мин.")
            else:
                lines.append(f"• @{name}")
        text = "\n".join(lines)

    await callback.message.edit_text(text, reply_markup=keyboards.paginated_user_list(rows, kind, offset, total))


async def _show_logs(callback: CallbackQuery, offset: int):
    rows = await db.get_logs(offset=offset)
    total = await db.count_logs()
    if not rows:
        text = "📜 <b>Логи</b>\n\nПока пусто."
    else:
        lines = ["📜 <b>Логи действий бота</b>", ""]
        for r in rows:
            ts = time.strftime("%d.%m %H:%M", time.localtime(r["ts"]))
            lines.append(f"[{ts}] {r['action']} — user:{r['user_id']} {r['details']}")
        text = "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=keyboards.logs_list(offset, total))
