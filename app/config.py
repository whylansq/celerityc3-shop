from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(ValueError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_telegram_id: int
    celerity_base_url: str
    celerity_api_key: str
    celerity_auth_mode: str
    celerity_timeout_seconds: float
    payment_card: str
    payment_card_holder: str
    support_username: str
    database_path: str
    log_file_path: str
    log_level: str

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        def required(name: str) -> str:
            value = os.getenv(name, "").strip()
            if not value:
                raise ConfigError(f"Required environment variable is missing: {name}")
            return value

        try:
            admin_id = int(required("ADMIN_TELEGRAM_ID"))
        except ValueError as exc:
            raise ConfigError("ADMIN_TELEGRAM_ID must be a Telegram numeric ID") from exc

        auth_mode = os.getenv("CELERITY_AUTH_MODE", "bearer").strip().lower()
        if auth_mode not in {"bearer", "x-api-key"}:
            raise ConfigError("CELERITY_AUTH_MODE must be bearer or x-api-key")

        try:
            timeout = float(os.getenv("CELERITY_TIMEOUT_SECONDS", "30"))
        except ValueError as exc:
            raise ConfigError("CELERITY_TIMEOUT_SECONDS must be a number") from exc
        if timeout <= 0:
            raise ConfigError("CELERITY_TIMEOUT_SECONDS must be greater than zero")

        base_url = required("CELERITY_BASE_URL").rstrip("/")
        if not base_url.startswith(("https://", "http://")):
            raise ConfigError("CELERITY_BASE_URL must start with http:// or https://")

        database_path = os.getenv("DATABASE_PATH", "/app/data/shop.db").strip()
        if not database_path:
            raise ConfigError("DATABASE_PATH must not be empty")

        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        log_file_path = os.getenv(
            "LOG_FILE_PATH", "/app/logs/vpn-shop-bot.log"
        ).strip()
        if not log_file_path:
            raise ConfigError("LOG_FILE_PATH must not be empty")
        Path(log_file_path).parent.mkdir(parents=True, exist_ok=True)

        return cls(
            bot_token=required("BOT_TOKEN"),
            admin_telegram_id=admin_id,
            celerity_base_url=base_url,
            celerity_api_key=required("CELERITY_API_KEY"),
            celerity_auth_mode=auth_mode,
            celerity_timeout_seconds=timeout,
            payment_card=required("PAYMENT_CARD"),
            payment_card_holder=required("PAYMENT_CARD_HOLDER"),
            support_username=os.getenv("SUPPORT_USERNAME", "").strip().lstrip("@"),
            database_path=database_path,
            log_file_path=log_file_path,
            log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        )