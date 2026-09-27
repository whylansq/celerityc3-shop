from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    id: str
    name: str
    days: int
    price_rub: int


# The source of truth for the shop catalogue. Add or edit plans here.
PLANS: tuple[Plan, ...] = (
    Plan(id="30d", name="30 дней", days=30, price_rub=300),
    Plan(id="90d", name="90 дней", days=90, price_rub=750),
    Plan(id="180d", name="180 дней", days=180, price_rub=1300),
)


def get_plan(plan_id: str) -> Plan | None:
    return next((plan for plan in PLANS if plan.id == plan_id), None)


def format_price(price_rub: int) -> str:
    return f"{price_rub:,}".replace(",", " ") + " ₽"