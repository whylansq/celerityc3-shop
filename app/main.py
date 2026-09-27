from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .celerity import CelerityClient, CelerityError
from .config import Config, ConfigError
from .database import Database, utc_now
from .formatting import display_date, order_text, parse_datetime, rejection_text
from .keyboards import (
    MAIN_KEYBOARD,
    admin_retry_keyboard,
    admin_review_keyboard,
    existing_order_keyboard,
    payment_keyboard,
    plans_keyboard,
)
from .plans import Plan, get_plan, format_price


LOGGER = logging.getLogger("vpn_shop_bot")


class BotRuntime:
    def __init__(self, config: Config):
        self.config = config
        self.db = Database(config.database_path)
        self.celerity = CelerityClient(config)


def get_runtime(application: Application) -> BotRuntime:
    runtime = application.bot_data.get("runtime")
    if not isinstance(runtime, BotRuntime):
        raise RuntimeError("Bot runtime is not initialized")
    return runtime


def is_admin(update: Update, runtime: BotRuntime) -> bool:
    return bool(update.effective_user and update.effective_user.id == runtime.config.admin_telegram_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context.application)
    user = update.effective_user
    if user is None or update.message is None:
        return
    runtime.db.upsert_user(user.id, user.username)
    context.user_data.clear()
    await update.message.reply_text(
        "VPN SHOP\n\nДобро пожаловать!\nВыберите действие:",
        reply_markup=MAIN_KEYBOARD,
    )


async def payment_support(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context.application)
    username = runtime.config.support_username
    contact = f"@{username}" if username else "контакт поддержки не настроен"
    if update.message:
        await update.message.reply_text(
            "Если возникли проблемы с оплатой или подключением, "
            f"напишите в поддержку:\n{contact}",
            reply_markup=MAIN_KEYBOARD,
        )


async def show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "Выберите тариф VPN:"
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=plans_keyboard())
    elif update.message:
        await update.message.reply_text(text, reply_markup=plans_keyboard())


async def plan_selected(
    query: Any, runtime: BotRuntime, context: ContextTypes.DEFAULT_TYPE, plan: Plan
) -> None:
    context.user_data["selected_plan_id"] = plan.id
    text = (
        f"Вы выбрали:\n\nVPN-доступ\n{plan.name}\n"
        f"Стоимость: {format_price(plan.price_rub)}\n\n"
        "Для оплаты переведите указанную сумму по реквизитам:\n\n"
        f"Номер карты:\n<code>{runtime.config.payment_card}</code>\n\n"
        f"Получатель:\n{runtime.config.payment_card_holder}\n\n"
        "После оплаты нажмите кнопку:"
    )
    await query.edit_message_text(
        text, parse_mode=ParseMode.HTML, reply_markup=payment_keyboard(plan.id)
    )


async def paid_selected(
    query: Any,
    runtime: BotRuntime,
    context: ContextTypes.DEFAULT_TYPE,
    plan: Plan,
    *,
    force_new: bool = False,
) -> None:
    user = query.from_user
    runtime.db.upsert_user(user.id, user.username)
    order, existing = runtime.db.create_order(
        user.id,
        user.username,
        plan.id,
        plan.name,
        plan.price_rub,
        plan.days,
        allow_duplicate=force_new,
    )
    context.user_data["selected_plan_id"] = plan.id
    if existing and not force_new:
        LOGGER.info("Reusing open order %s", order["order_id"])
        status = order.get("status")
        if status == "pending_review":
            await query.edit_message_text(
                f"Заявка <code>{order['order_id']}</code> уже передана "
                "администратору на проверку.\n\n"
                "Если это новый перевод, создайте отдельную заявку.",
                parse_mode=ParseMode.HTML,
                reply_markup=existing_order_keyboard(plan.id),
            )
            return
        context.user_data["awaiting_receipt_order_id"] = order["id"]
        await query.edit_message_text(
            f"Заявка <code>{order['order_id']}</code> уже создана.\n\n"
            "Отправьте чек или скриншот одним сообщением.",
            parse_mode=ParseMode.HTML,
        )
        return

    context.user_data["awaiting_receipt_order_id"] = order["id"]
    LOGGER.info("Order created %s", order["order_id"])
    await query.edit_message_text(
        f"Заявка <code>{order['order_id']}</code> создана.\n\n"
        "Отправьте чек или скриншот оплаты одним сообщением.\n"
        "После получения чек будет передан администратору на проверку.",
        parse_mode=ParseMode.HTML,
    )


def valid_document(document: Any) -> bool:
    mime = (document.mime_type or "").lower()
    name = (document.file_name or "").lower()
    return (
        mime == "application/pdf"
        or mime.startswith("image/")
        or name.endswith((".pdf", ".png", ".jpg", ".jpeg", ".webp"))
    )


async def receipt_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context.application)
    if update.message is None or update.effective_user is None:
        return
    order_id = context.user_data.get("awaiting_receipt_order_id")
    if not order_id:
        await update.message.reply_text(
            "Сначала выберите тариф и нажмите «✅ Я оплатил».",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    file_id: str | None = None
    file_type: str | None = None
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        file_type = "photo"
    elif update.message.document:
        if not valid_document(update.message.document):
            await update.message.reply_text("Примите только PDF или изображение.")
            return
        file_id = update.message.document.file_id
        file_type = "document"

    if not file_id or not file_type:
        return
    if not runtime.db.attach_receipt(int(order_id), file_id, file_type):
        context.user_data.pop("awaiting_receipt_order_id", None)
        await update.message.reply_text(
            "Эта заявка уже закрыта. Если нужен новый платёж, начните покупку заново.",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    context.user_data.pop("awaiting_receipt_order_id", None)
    order = runtime.db.get_order(int(order_id))
    if order is None:
        await update.message.reply_text("Заявка не найдена. Обратитесь в поддержку.")
        return
    LOGGER.info("Receipt received for order %s (%s)", order["order_id"], file_type)

    try:
        await context.bot.send_message(
            runtime.config.admin_telegram_id,
            order_text(order),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_review_keyboard(int(order["id"])),
        )
        if file_type == "photo":
            await context.bot.send_photo(runtime.config.admin_telegram_id, file_id)
        else:
            await context.bot.send_document(runtime.config.admin_telegram_id, file_id)
    except Exception:
        LOGGER.exception("Failed to notify administrator for order %s", order["id"])

    await update.message.reply_text(
        f"✅ Чек по заявке <code>{order['order_id']}</code> получен.\n"
        "Администратор проверит оплату вручную.",
        parse_mode=ParseMode.HTML,
        reply_markup=MAIN_KEYBOARD,
    )


async def show_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context.application)
    if update.message is None or update.effective_user is None:
        return
    user = runtime.db.get_user(update.effective_user.id)
    if not user or not user.get("subscription_token"):
        await update.message.reply_text(
            "У вас пока нет активной VPN-подписки.", reply_markup=MAIN_KEYBOARD
        )
        return
    expiry = parse_datetime(user.get("expires_at"))
    if expiry is None:
        await update.message.reply_text(
            "Данные подписки пока недоступны. Обратитесь в поддержку.",
            reply_markup=MAIN_KEYBOARD,
        )
        return
    seconds_left = (expiry - datetime.now(timezone.utc)).total_seconds()
    status = "🟢 Активна" if seconds_left > 0 else "🔴 Истекла"
    days_left = max(0, math.ceil(seconds_left / 86400))
    url = runtime.celerity.subscription_url(str(user["subscription_token"]))
    await update.message.reply_text(
        "📋 Ваша подписка\n\n"
        f"Статус: {status}\n"
        f"Действует до: {display_date(user['expires_at'])}\n"
        f"Дней осталось: {days_left}\n\n"
        f"Ссылка:\n{url}",
        reply_markup=MAIN_KEYBOARD,
    )


async def how_to_connect(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(
            "📱 Как подключиться\n\n"
            "1. Установите совместимый VPN-клиент.\n"
            "2. Скопируйте ссылку подписки из сообщения о выдаче доступа.\n"
            "3. Добавьте ссылку в клиент как подписку.\n"
            "4. Обновите подписку и выберите сервер Финляндии.\n\n"
            "Если нужна помощь, обратитесь в поддержку.",
            reply_markup=MAIN_KEYBOARD,
        )


async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context.application)
    if not is_admin(update, runtime):
        if update.message:
            await update.message.reply_text("⛔ Нет доступа")
        return
    if update.message:
        await update.message.reply_text(
            "📊 VPN Shop\n\n"
            f"Пользователей: {runtime.db.count_users()}\n"
            f"Всего заказов: {runtime.db.count_orders()}\n"
            f"Ожидают проверки: {runtime.db.count_orders_by_status('pending_review')}\n"
            f"Активных подписок: {runtime.db.count_active_subscriptions()}"
        )


async def pending_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context.application)
    if not is_admin(update, runtime):
        if update.message:
            await update.message.reply_text("⛔ Нет доступа")
        return
    orders = runtime.db.list_orders(("pending_review", "failed"))
    if update.message is None:
        return
    if not orders:
        await update.message.reply_text("Ожидающих заявок нет.")
        return
    for order in orders:
        keyboard = (
            admin_review_keyboard(order["id"])
            if order["status"] == "pending_review"
            else admin_retry_keyboard(order["id"])
        )
        text = order_text(order) if order["status"] == "pending_review" else (
            f"⚠️ <b>Ошибка выдачи</b>\n\n"
            f"Заказ: <code>{order['order_id']}</code>\n"
            f"Ошибка: {order.get('error_message') or 'неизвестная ошибка'}"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def retry_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context.application)
    if not is_admin(update, runtime):
        if update.message:
            await update.message.reply_text("⛔ Нет доступа")
        return
    orders = runtime.db.list_orders(("failed",))
    if update.message is None:
        return
    if not orders:
        await update.message.reply_text("Заказов для повторной выдачи нет.")
        return
    for order in orders:
        await update.message.reply_text(
            f"Заказ: <code>{order['order_id']}</code>\n"
            f"Ошибка: {order.get('error_message') or 'неизвестная ошибка'}",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_retry_keyboard(order["id"]),
        )


async def provision_order(
    application: Application, runtime: BotRuntime, numeric_id: int
) -> None:
    order = runtime.db.get_order(numeric_id)
    if not order or not runtime.db.mark_provisioning(numeric_id):
        return
    order = runtime.db.get_order(numeric_id)
    assert order is not None
    stable_id = f"tg_{order['telegram_user_id']}"
    try:
        current = await runtime.celerity.find_user(stable_id)
        current_expiry = parse_datetime(current.get("expireAt")) if current else None
        target = parse_datetime(order.get("provisioning_target_expires_at"))
        if target is None:
            base = current_expiry if current_expiry and current_expiry > datetime.now(timezone.utc) else datetime.now(timezone.utc)
            target = base + timedelta(days=int(order["duration_days"]))
            runtime.db.set_provisioning_target(numeric_id, target.isoformat())

        celerity_user_id = str(
            (current or {}).get("userId")
            or (current or {}).get("_id")
            or stable_id
        )
        result: dict[str, Any] = current or {}
        if current is None:
            LOGGER.info("Creating Celerity user for order %s", order["order_id"])
            result = await runtime.celerity.create_user(
                {
                    "userId": stable_id,
                    "enabled": True,
                    "expireAt": target.isoformat(),
                }
            )
        else:
            needs_update = (
                current_expiry is None
                or current_expiry < target - timedelta(seconds=2)
                or not bool(current.get("enabled", True))
            )
            if needs_update:
                LOGGER.info("Extending Celerity user for order %s", order["order_id"])
                result = await runtime.celerity.update_user(
                    celerity_user_id,
                    {"expireAt": target.isoformat(), "enabled": True},
                )

        refreshed = await runtime.celerity.find_user(stable_id)
        if refreshed:
            result = {**result, **refreshed}
        token = str(
            result.get("subscriptionToken")
            or (current or {}).get("subscriptionToken")
            or ""
        )
        if not token:
            raise CelerityError("Celerity did not return subscriptionToken")
        expires_at = str(result.get("expireAt") or target.isoformat())
        runtime.db.save_celerity_state(
            numeric_id,
            int(order["telegram_user_id"]),
            celerity_user_id,
            token,
            expires_at,
        )
        if not runtime.db.mark_completed(numeric_id):
            raise CelerityError("Order was changed before completion")

        url = runtime.celerity.subscription_url(token)
        try:
            await application.bot.send_message(
                int(order["telegram_user_id"]),
                "🎉 Оплата подтверждена!\n\n"
                "VPN-доступ активирован.\n\n"
                f"📦 Тариф: {order['plan_name']}\n"
                f"📅 Действует до: {display_date(expires_at)}\n\n"
                f"🔗 Ваша ссылка подписки:\n{url}\n\n"
                "Добавьте эту ссылку в ваш VPN-клиент.",
                reply_markup=MAIN_KEYBOARD,
            )
        except Exception:
            LOGGER.exception(
                "Access was provisioned but user notification failed for order %s",
                order["order_id"],
            )
            await application.bot.send_message(
                runtime.config.admin_telegram_id,
                f"✅ Доступ выдан по заказу {order['order_id']}, "
                "но уведомление пользователю не доставлено.",
            )
        else:
            await application.bot.send_message(
                runtime.config.admin_telegram_id,
                f"✅ Доступ выдан по заказу {order['order_id']}.",
            )
        LOGGER.info("Provisioning completed for order %s", order["order_id"])
    except Exception as exc:
        runtime.db.mark_failed(numeric_id, str(exc))
        LOGGER.exception("Provisioning failed for order %s", order["order_id"])
        await application.bot.send_message(
            runtime.config.admin_telegram_id,
            "⚠️ Оплата подтверждена, но Celerity временно недоступен.\n\n"
            f"Доступ автоматически не выдан.\n"
            f"Заказ: {order['order_id']}\n"
            f"Ошибка: {str(exc)[:500]}",
            reply_markup=admin_retry_keyboard(numeric_id),
        )


async def callback_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    runtime = get_runtime(context.application)
    data = query.data or ""

    if data.startswith("plan:"):
        plan = get_plan(data.removeprefix("plan:"))
        if plan:
            await plan_selected(query, runtime, context, plan)
        return
    if data.startswith(("paid:", "neworder:", "useorder:")):
        prefix, plan_id = data.split(":", 1)
        plan = get_plan(plan_id)
        if not plan:
            await query.edit_message_text("Тариф больше недоступен.")
            return
        if prefix == "useorder":
            user = query.from_user
            order, _ = runtime.db.create_order(
                user.id, user.username, plan.id, plan.name, plan.price_rub, plan.days
            )
            context.user_data["awaiting_receipt_order_id"] = order["id"]
            await query.edit_message_text(
                f"Отправьте чек по заявке <code>{order['order_id']}</code>.",
                parse_mode=ParseMode.HTML,
            )
        else:
            await paid_selected(
                query, runtime, context, plan, force_new=prefix == "neworder"
            )
        return

    if not is_admin(update, runtime):
        await query.answer("⛔ Нет доступа", show_alert=True)
        return
    try:
        numeric_id = int(data.split(":", 1)[1])
    except (IndexError, ValueError):
        await query.edit_message_text("Некорректная заявка.")
        return

    if data.startswith("approve:"):
        state = runtime.db.claim_for_approval(numeric_id, runtime.config.admin_telegram_id)
        if state == "completed":
            await query.edit_message_text("Заказ уже выполнен.")
        elif state != "claimed":
            await query.edit_message_text(f"Заказ нельзя подтвердить: {state}.")
        else:
            LOGGER.info("Payment approved for order id=%s", numeric_id)
            await query.edit_message_text("⏳ Оплата подтверждена, выдаю доступ…")
            await provision_order(context.application, runtime, numeric_id)
        return
    if data.startswith("retry:"):
        state = runtime.db.claim_failed_for_retry(
            numeric_id, runtime.config.admin_telegram_id
        )
        if state != "claimed":
            await query.edit_message_text("Эту заявку уже нельзя повторить.")
        else:
            LOGGER.info("Retrying provisioning for order id=%s", numeric_id)
            await query.edit_message_text("⏳ Повторяю выдачу доступа…")
            await provision_order(context.application, runtime, numeric_id)
        return
    if data.startswith("reject:"):
        order = runtime.db.get_order(numeric_id)
        if not order or order["status"] != "pending_review":
            await query.edit_message_text("Эту заявку уже нельзя отклонить.")
            return
        context.user_data["admin_reject_order_id"] = numeric_id
        await query.edit_message_text(
            f"Введите причину отклонения заявки {order['order_id']} одним сообщением."
        )


async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = get_runtime(context.application)
    if update.message is None or update.effective_user is None:
        return
    text = (update.message.text or "").strip()
    if is_admin(update, runtime) and context.user_data.get("admin_reject_order_id"):
        numeric_id = int(context.user_data.pop("admin_reject_order_id"))
        if runtime.db.reject_order(numeric_id, runtime.config.admin_telegram_id, text):
            order = runtime.db.get_order(numeric_id)
            if order:
                LOGGER.info("Payment rejected for order %s", order["order_id"])
                await context.bot.send_message(
                    int(order["telegram_user_id"]),
                    rejection_text(text),
                    parse_mode=ParseMode.HTML,
                    reply_markup=MAIN_KEYBOARD,
                )
                await update.message.reply_text("Заявка отклонена.")
        else:
            await update.message.reply_text("Заявка уже обработана.")
        return

    if text in {"🛒 Купить VPN", "🔄 Продлить"}:
        await show_plans(update, context)
    elif text == "📋 Моя подписка":
        await show_subscription(update, context)
    elif text == "📱 Как подключиться":
        await how_to_connect(update, context)
    elif text == "🆘 Поддержка":
        await payment_support(update, context)
    elif context.user_data.get("awaiting_receipt_order_id"):
        await update.message.reply_text("Отправьте чек как фото, PDF или изображение.")
    else:
        await update.message.reply_text("Выберите действие в меню.", reply_markup=MAIN_KEYBOARD)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.exception("Unhandled Telegram error", exc_info=context.error)


def build_application(config: Config) -> Application:
    runtime = BotRuntime(config)
    runtime.db.initialize()
    application = ApplicationBuilder().token(config.bot_token).build()
    application.bot_data["runtime"] = runtime
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("paysupport", payment_support))
    application.add_handler(CommandHandler("admin", admin_dashboard))
    application.add_handler(CommandHandler("pending", pending_orders))
    application.add_handler(CommandHandler("retry_pending", retry_pending))
    application.add_handler(CallbackQueryHandler(callback_router))
    application.add_handler(
        MessageHandler(filters.PHOTO | filters.Document.ALL, receipt_received)
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_router)
    )
    application.add_error_handler(error_handler)
    return application


def main() -> None:
    try:
        config = Config.from_env()
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc
    Path(config.log_file_path).parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(config.log_file_path, encoding="utf-8"),
        ],
    )
    LOGGER.info("VPN Shop Bot starting in long-polling mode")
    build_application(config).run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False,
    )


if __name__ == "__main__":
    main()