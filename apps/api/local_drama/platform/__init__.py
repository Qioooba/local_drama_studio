"""Cross-platform operating-system capability ports and adapters."""

from .registry import PlatformServices, create_platform_services

__all__ = ["PlatformServices", "create_platform_services"]
