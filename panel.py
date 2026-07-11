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
import giveaways

router = Router(name="panel")


async def _is_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if user_id == config.SUPER_ADMIN_ID:
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("creator", "administrator")
    except Exception:
        return False


async def _is_moderator_or_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    if await _is_admin(bot, chat_id, user_id):
        return True
    return await db.user_has_permission(user_id, "manage_reports")


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
            "Настройки в чате: /setflood <лимит> <окно_сек>, /setwarnexpiry <дней>, "
            "/addword /delword /words (стоп-слова), /adddomain /deldomain /domains (домены).",
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

    elif section == "reports":
        offset = int(parts[2])
        await _show_reports(callback, offset)

    elif section == "report_view":
        report_id = int(parts[2])
        await _show_report_detail(callback, report_id)

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

    elif section == "antiraid":
        if len(parts) == 2:
            status = "🔒 Чат ЗАБЛОКИРОВАН" if await db.get_bool_setting("lockdown_active") else "🔓 Чат в обычном режиме"
            await callback.message.edit_text(
                f"🚨 <b>Антирейд</b>\n\n{status}", reply_markup=await keyboards.antiraid_menu()
            )
        elif parts[2] == "toggle":
            key = parts[3]
            current = await db.get_bool_setting(key)
            await db.set_setting(key, "0" if current else "1")
            await db.add_log("settings_change", callback.from_user.id, callback.from_user.id, f"{key} -> {not current}")
            status = "🔒 Чат ЗАБЛОКИРОВАН" if await db.get_bool_setting("lockdown_active") else "🔓 Чат в обычном режиме"
            await callback.message.edit_text(
                f"🚨 <b>Антирейд</b>\n\n{status}", reply_markup=await keyboards.antiraid_menu()
            )
            await callback.answer("Обновлено ✅")
        elif parts[2] == "info":
            await callback.answer(
                "Изменить порог можно только напрямую в БД (raid_join_threshold, raid_window_sec) — "
                "или попросите разработчика добавить команду /setraid.",
                show_alert=True,
            )
        elif parts[2] == "lockdown":
            engaged = await punishments.engage_lockdown(bot, callback.message.chat.id, f"вручную из панели ({callback.from_user.id})")
            await callback.answer("🔒 Чат заблокирован." if engaged else "⚠️ Не удалось заблокировать чат.", show_alert=True)
            await callback.message.edit_text(
                "🚨 <b>Антирейд</b>\n\n🔒 Чат ЗАБЛОКИРОВАН", reply_markup=await keyboards.antiraid_menu()
            )
        elif parts[2] == "unlock":
            lifted = await punishments.lift_lockdown(bot, callback.message.chat.id)
            await callback.answer("🔓 Блокировка снята." if lifted else "⚠️ Не удалось снять блокировку.", show_alert=True)
            await callback.message.edit_text(
                "🚨 <b>Антирейд</b>\n\n🔓 Чат в обычном режиме", reply_markup=await keyboards.antiraid_menu()
            )

    elif section == "giveaways":
        rows = await db.list_active_giveaways()
        text = await _render_giveaways_text(rows)
        await callback.message.edit_text(text, reply_markup=keyboards.giveaways_panel_list(rows))

    elif section == "giveaway_end":
        giveaway_id = int(parts[2])
        finished = await giveaways.finish_giveaway(bot, giveaway_id)
        await callback.answer("✅ Розыгрыш завершён." if finished else "Этот розыгрыш уже неактивен.", show_alert=True)
        rows = await db.list_active_giveaways()
        text = await _render_giveaways_text(rows)
        await callback.message.edit_text(text, reply_markup=keyboards.giveaways_panel_list(rows))

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


async def _show_reports(callback: CallbackQuery, offset: int):
    rows = await db.get_open_reports(offset=offset)
    total = await db.count_open_reports()
    if not rows:
        text = "📨 <b>Жалобы</b>\n\nОткрытых жалоб нет."
    else:
        lines = ["📨 <b>Открытые жалобы</b>", ""]
        for r in rows:
            ts = time.strftime("%d.%m %H:%M", time.localtime(r["created_at"]))
            lines.append(f"#{r['id']} [{ts}] на user:{r['target_id']} — {r['reason']}")
        text = "\n".join(lines)
    await callback.message.edit_text(text, reply_markup=keyboards.reports_list(rows, offset, total))


async def _show_report_detail(callback: CallbackQuery, report_id: int):
    r = await db.get_report(report_id)
    if r is None:
        await callback.answer("Жалоба не найдена.", show_alert=True)
        return
    text = (
        f"📨 <b>Жалоба #{r['id']}</b>\n\n"
        f"От: user:{r['reporter_id']}\n"
        f"На: user:{r['target_id']}\n"
        f"Причина: {r['reason']}\n"
        f"Статус: {r['status']}\n\n"
        f"Сообщение: <i>{r['message_snippet']}</i>"
    )
    if r["status"] == "open":
        await callback.message.edit_text(text, reply_markup=keyboards.report_action_keyboard(report_id))
    else:
        await callback.message.edit_text(text, reply_markup=keyboards.back_only_menu("panel:reports:0"))


@router.callback_query(F.data.startswith("report:"))
async def report_action_router(callback: CallbackQuery, bot: Bot):
    """Кнопки быстрого реагирования под уведомлением о жалобе в чате."""
    if not await _is_moderator_or_admin(bot, callback.message.chat.id, callback.from_user.id):
        await callback.answer("⛔ Только для администраторов и модераторов", show_alert=True)
        return

    _, action, report_id = callback.data.split(":")
    report_id = int(report_id)
    r = await db.get_report(report_id)
    if r is None:
        await callback.answer("Жалоба не найдена (возможно, уже обработана).", show_alert=True)
        return
    if r["status"] != "open":
        await callback.answer(f"Жалоба уже обработана ({r['status']}).", show_alert=True)
        return

    chat_id = callback.message.chat.id
    target_id = r["target_id"]

    if action == "mute":
        await punishments.mute_user(bot, chat_id, target_id, 30 * 60, "по жалобе")
        await db.resolve_report(report_id, "resolved", callback.from_user.id)
        await db.add_log("report_resolved", target_id, callback.from_user.id, f"report_id={report_id} action=mute")
        await callback.message.edit_text(callback.message.text + "\n\n✅ Обработано: мут 30 мин.", reply_markup=None)

    elif action == "ban":
        await punishments.ban_user(bot, chat_id, target_id, "по жалобе", callback.from_user.id)
        await db.resolve_report(report_id, "resolved", callback.from_user.id)
        await db.add_log("report_resolved", target_id, callback.from_user.id, f"report_id={report_id} action=ban")
        await callback.message.edit_text(callback.message.text + "\n\n✅ Обработано: бан.", reply_markup=None)

    elif action == "dismiss":
        await db.resolve_report(report_id, "dismissed", callback.from_user.id)
        await db.add_log("report_dismissed", target_id, callback.from_user.id, f"report_id={report_id}")
        await callback.message.edit_text(callback.message.text + "\n\n❌ Жалоба отклонена.", reply_markup=None)

    await callback.answer("Готово ✅")


async def _render_giveaways_text(rows) -> str:
    if not rows:
        return "🎉 <b>Розыгрыши</b>\n\nАктивных розыгрышей нет.\n\nЗапустить: /giveaway <минут> <приз> в чате."
    now = int(time.time())
    lines = ["🎉 <b>Активные розыгрыши</b>", ""]
    for g in rows:
        count = await db.count_giveaway_participants(g["id"])
        remaining_min = max(0, (g["ends_at"] - now) // 60)
        lines.append(f"#{g['id']} — {g['prize']} — участников: {count}, осталось ~{remaining_min} мин.")
    return "\n".join(lines)
