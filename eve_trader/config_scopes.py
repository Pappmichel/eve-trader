"""The one place that maps a `tenant_settings.scope` to its config dataclass.

Lives outside config.py because that module must not import the tool config
modules (they import it). Used by the startup collision warning in
api/app.py and by `eve-trader admin prune-settings`; the per-tool
`resolve_and_set_*` functions know their own class and pass it directly.
"""

from __future__ import annotations


def tenant_config_classes() -> dict[str, type]:
    from .config import TradingConfig
    from .doctrine.config import DoctrineConfig
    from .module_reprocessing.config import ModuleReprocessingConfig
    from .pi.config import PiConfig
    from .production.config import ProductionConfig
    from .refining.config import RefiningConfig
    from .station_trading.config import StationTradingConfig

    return {
        "trading": TradingConfig,
        "production": ProductionConfig,
        "doctrine": DoctrineConfig,
        "pi": PiConfig,
        "refining": RefiningConfig,
        "station_trading": StationTradingConfig,
        "module_reprocessing": ModuleReprocessingConfig,
    }
