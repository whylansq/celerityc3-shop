from __future__ import annotations

from datetime import datetime, timezone
from html import escape
from typing import Any

from .plans import format_price


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def display_date(value: str | None) -> str:
    parsed = parse_datetime(value)
    return parsed.astimezone(timezone.utc).strftime("%d.%m.%Y") if parsed else "—"


def order_text(order: dict[str, Any]) -> str:
    username = order.get("telegram_username")
    user_label = f"@{username}" if username else "без username"
    return (
        "💰 <b>НОВАЯ ЗАЯВКА</b>\n\n"
        f"Заказ: <code>{escape(str(order.get('order_id', '—')))}</code>\n"
        f"👤 Пользователь: {escape(user_label)}\n"
        f"Telegram ID: <code>{order.get('telegram_user_id')}</code>\n\n"
        f"📦 Тариф: {escape(str(order.get('plan_name')))}\n"
        f"💵 Сумма: {format_price(int(order.get('price', 0)))}\n"
        f"🕐 Создан: {escape(str(order.get('created_at', '—')))}\n\n"
        "Статус: ожидает проверки."
    )


def rejection_text(reason: str) -> str:
    return (
        "❌ <b>Оплата не подтверждена.</b>\n\n"
        f"Причина: {escape(reason)}\n\n"
        "Если вы считаете, что произошла ошибка, обратитесь в поддержку."
    )