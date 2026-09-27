from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup

from .plans import PLANS, format_price


MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["🛒 Купить VPN", "📋 Моя подписка"],
        ["🔄 Продлить", "📱 Как подключиться"],
        ["🆘 Поддержка"],
    ],
    resize_keyboard=True,
)


def plans_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"{plan.name} — {format_price(plan.price_rub)}",
                    callback_data=f"plan:{plan.id}",
                )
            ]
            for plan in PLANS
        ]
    )


def payment_keyboard(plan_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("✅ Я оплатил", callback_data=f"paid:{plan_id}")]]
    )


def existing_order_keyboard(plan_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "📎 Отправить чек по этой заявке",
                    callback_data=f"useorder:{plan_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    "🆕 Это новый перевод",
                    callback_data=f"neworder:{plan_id}",
                )
            ],
        ]
    )


def admin_review_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✅ Подтвердить оплату", callback_data=f"approve:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "❌ Отклонить", callback_data=f"reject:{order_id}"
                )
            ],
        ]
    )


def admin_retry_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔄 Повторить выдачу", callback_data=f"retry:{order_id}"
                )
            ]
        ]
    )