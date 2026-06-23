"""Unit tests for admin panel backend settings defaults.

Validates: Requirements 6.10, 20.1, 20.2, 22.6
"""

from decimal import Decimal

from app.core.config import Settings, get_settings


def test_admin_config_defaults():
    # get_settings() is lru_cache'd; instantiate Settings directly so the
    # field defaults are asserted independently of any cached instance.
    # Required fields (SECRET_KEY, DATABASE_URL) are provided via env in conftest.
    settings = Settings()

    assert settings.ADMIN_WALLET_RISK_THRESHOLD == Decimal("100")
    assert settings.ADMIN_DEFAULT_PAGE_SIZE == 25
    assert settings.ADMIN_MAX_PAGE_SIZE == 100
    assert settings.ADMIN_MAX_BULK_RECORDS == 100
    assert settings.ADMIN_ACCESS_TOKEN_EXPIRE_MINUTES == 30
    assert settings.ADMIN_REFRESH_TOKEN_EXPIRE_DAYS == 7
    assert settings.ADMIN_LOGIN_MAX_ATTEMPTS == 5
    assert settings.ADMIN_LOGIN_WINDOW_SECONDS == 900
    assert settings.ADMIN_LOGIN_LOCKOUT_SECONDS == 900
    assert settings.ADMIN_RATE_LIMIT_PER_MINUTE == 120


def test_get_settings_exposes_admin_defaults():
    settings = get_settings()

    assert settings.ADMIN_WALLET_RISK_THRESHOLD == Decimal("100")
    assert settings.ADMIN_DEFAULT_PAGE_SIZE == 25
    assert settings.ADMIN_MAX_PAGE_SIZE == 100
    assert settings.ADMIN_MAX_BULK_RECORDS == 100
