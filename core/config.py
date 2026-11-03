# core/config.py
# właściwa implementacja ustawień przeniesiona do config/config.py

from config.config import (  # noqa: F401
    AppSettings,
    DBSettings,
    KeycloakSettings,
    LoggingSettings,
    Settings,
    section,
    settings,
)

__all__ = [
    'AppSettings',
    'DBSettings',
    'KeycloakSettings',
    'LoggingSettings',
    'Settings',
    'section',
    'settings',
]
