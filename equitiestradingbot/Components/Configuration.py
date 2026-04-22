import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from datetime import datetime
from collections.abc import MutableMapping

import toml

DEFAULT_CONFIGURATION_PATH = Path.home() / "Documents" / "equitiestradingbot" / "config" / "live_trading_bot.toml"
CONFIGURATION_ROOT = "trading_bot_root"

Property = Any
ConfigDict = MutableMapping[str, Property]
CredentialsDict = Dict[str, str]

class Configuration:
    config: ConfigDict

    def __init__(self, dictionary: ConfigDict) -> None:
        if not isinstance(dictionary,dict):
            raise ValueError("argument must be a dict")
        self.config = self._parse_raw_config(dictionary)
        logging.info("Configuration Loaded")

    
    @staticmethod
    def from_filepath(filepath: Optional[Path]) -> "Configuration":
        filepath = filepath if filepath else DEFAULT_CONFIGURATION_PATH
        logging.debug("Loading Configuration: {}".format(filepath))
        with filepath.open(mode="r") as f:
            return Configuration(toml.load(f))
    
    def _find_property(self, fields: List[str]) -> Union[ConfigDict, Property]:
        if CONFIGURATION_ROOT in fields:
            return self.config
        if type(fields) is not list or len(fields) < 1:
            raise ValueError("Can't find properties {} in configuration".format(fields))
        value = self.config[fields[0]]
        for f in fields[1:]:
            value = value[f]
        return value
    
    def _parse_raw_config(self,config_dict: ConfigDict) -> ConfigDict:
        config_copy = config_dict
        for key, value in config_copy.items():
            if type(value) is dict:
                config_dict[key] = self._parse_raw_config(value)
            elif type(value) is list:
                for i in range(len(value)):
                    config_dict[key][i]= (
                        self._replace_placeholders(config_dict[key][i])
                        if type(config_dict[key][i]) is str
                        else config_dict[key][i]
                    )
            elif type(value) is str:
                config_dict[key] = self._replace_placeholders(config_dict[key])
        return config_dict
    
    def _replace_placeholders(self, string: str) -> str:
        string = string.replace("{home}", str(Path.home()))
        string = string.replace(
            "{timestamp}",
            datetime.now().isoformat().replace(":", "_").replace(".", "_"),
        )
        return string
    
    def get_raw_config(self) -> ConfigDict:
        return self._find_property([CONFIGURATION_ROOT])
    
    def get_max_account_usable(self) -> Property:
        return self._find_property(["max_account_usable"])
    
    def get_time_zone(self) -> Property:
        return self._find_property(["time_zone"])
    
    def get_credentials_filepath(self) -> Property:
        return self._find_property(["credentials_filepath"])
    
    def get_telegram_credentials_filepath(self) -> Property:
        return self._find_property(["telegram_credentials_filepath"])
    
    def get_credentials(self) -> CredentialsDict:
        with Path(self.get_credentials_filepath()).open(mode="r") as f:
            return json.load(f)
        
    def get_telegram_credentials(self) -> CredentialsDict:
        with Path(self.get_telegram_credentials_filepath()).open(mode="r") as f:
            return json.load(f)
        
    def get_spin_interval(self) -> Property:
        return self._find_property(["spin_interval"])
    
    def is_logging_enabled(self) -> Property: 
        return self._find_property(["logging", "enable"])
    
    def get_log_filepath(self) -> Property:
        return self._find_property(["logging", "log_filepath"])
    
    def is_logging_debug_enabled(self) -> Property:
        return self._find_property(["logging", "debug"])
    
    def get_active_market_source(self) -> Property:
        return self._find_property(["market_source", "active"])
    
    def get_market_source_values(self) -> Property: 
        return self._find_property(["market_source", "values"])
    
    def get_epic_ids_filepath(self) -> Property:
        return self._find_property(["market_source", "epic_id_list", "filepath"])
    
    def get_watchlist_name(self) -> Property:
        return self._find_property(["market_source", "watchlist", "name"])
    
    def get_active_stocks_interface(self) -> Property:
        return self._find_property(["stocks_interface", "active"])
    
    def get_stocks_interface_values(self) -> Property:
        return self._find_property(["stocks_interface", "values"])
    
    def get_ig_order_type(self) -> Property:
        return self._find_property(["stocks_interface", "ig_interface", "order_type"])
    
    def get_ig_order_size(self) -> Property:
        return self._find_property(["stocks_interface", "ig_interface", "order_size"])
    
    def get_ig_order_expiry(self) -> Property:
        return self._find_property(["stocks_interface", "ig_interface", "order_expiry"])
    
    def get_ig_order_currency(self) -> Property:
        return self._find_property(
            ["stocks_interface", "ig_interface", "order_currency"]
        )
    
    def get_ig_order_force_open(self) -> Property:
        return self._find_property(
            ["stocks_interface", "ig_interface", "order_force_open"]
        )
    
    def get_ig_use_g_stop(self) -> Property:
        return self._find_property(["stocks_interface", "ig_interface", "use_g_stop"])
    
    def get_ig_use_demo_account(self) -> Property:
        return self._find_property(
            ["stocks_interface", "ig_interface", "use_demo_account"]
        )
    
    def get_ig_controlled_risk(self):
        return self._find_property(
            ["stocks_interface", "ig_interface", "controlled_risk"]
        ) 
    
    def get_ig_api_timeout(self) -> Property:
        return self._find_property(["stocks_interface", "ig_interface", "api_timeout"])
    
    def is_paper_trading_enabled(self) -> Property:
        return self._find_property(["paper_trading"])
    
    def get_alphavantage_api_timeout(self) -> Property:
        return self._find_property(["stocks_interface", "alpha_vantage", "api_timeout"])
    
    def get_yfinance_api_timeout(self) -> Property:
        return self._find_property(["stocks_interface", "yfinance", "api_timeout"])
    
    def get_active_account_interface(self) -> Property:
        return self._find_property(["account_interface", "active"])
    
    def get_account_interface_values(self) -> Property:
        return self._find_property(["account_interface", "values"])
    
    def get_active_strategy(self) -> Property:
        return self._find_property(["strategies","active"])
    
    def get_strategies_values(self) -> Property:
        return self._find_property(["strategies", "values"])
    
    def get_telegram_signal_window(self) -> Property:
        return self._find_property(["telegram", "signal_window_hours"])

    def get_telegram_signal_history_filepath(self) -> Property:
        return self._find_property(["telegram", "signal_history_filepath"])
    
    # Platform integration methods
    def get_platform_config(self) -> Property:
        return self._find_property(["platform"])
    
    def get_platform_signal_window(self) -> Property:
        return self._find_property(["platform", "signal_window_hours"])
    
    def get_platform_signal_history_filepath(self) -> Property:
        return self._find_property(["platform", "signal_history_filepath"])

    # ── Agent / reflection settings ───────────────────────────────────────────

    def get_anthropic_api_key(self) -> Optional[str]:
        """
        Returns the Anthropic API key from the credentials file defined in
        [agent] anthropic_credentials_filepath, or None if not configured.
        """
        try:
            filepath = self._find_property(["agent", "anthropic_credentials_filepath"])
            with Path(filepath).open(mode="r") as f:
                return json.load(f).get("api_key")
        except Exception:
            return None

    def get_post_trade_reflection_n(self) -> int:
        """Reflect every N closed trades (0 = disabled)."""
        try:
            return int(self._find_property(["agent", "post_trade_reflection_every_n"]))
        except Exception:
            return 0

    def get_daily_reflection_hour_utc(self) -> int:
        """UTC hour for daily reflection trigger (-1 = disabled)."""
        try:
            return int(self._find_property(["agent", "daily_reflection_hour_utc"]))
        except Exception:
            return -1

    def get_weekly_reflection_day(self) -> int:
        """Day of week for weekly reflection (0=Mon … 6=Sun, -1 = disabled)."""
        try:
            return int(self._find_property(["agent", "weekly_reflection_day"]))
        except Exception:
            return -1

    def get_weekly_reflection_hour_utc(self) -> int:
        """UTC hour for weekly reflection trigger."""
        try:
            return int(self._find_property(["agent", "weekly_reflection_hour_utc"]))
        except Exception:
            return 20

    # ── Instrument universe settings ──────────────────────────────────────────

    def get_universe_filepath(self) -> str:
        """Path to instrument_universe.json (used when market_source.active = 'universe')."""
        try:
            return self._find_property(["market_source", "universe", "filepath"])
        except Exception:
            from pathlib import Path
            return str(Path.home() / "Documents" / "equitiestradingbot" / "data" / "instrument_universe.json")

    def get_scheduler_intervals(self) -> dict:
        """Return per-session spin intervals as a dict (falls back to BotScheduler defaults)."""
        try:
            raw = self._find_property(["scheduler"])
            return {
                "OVERLAP": int(raw.get("interval_overlap", 60)),
                "LONDON":  int(raw.get("interval_london",  120)),
                "NY":      int(raw.get("interval_ny",      120)),
                "ASIAN":   int(raw.get("interval_asian",   300)),
                "OFF":     int(raw.get("interval_off",     1800)),
            }
        except Exception:
            return {}

    def get_universe_news_filter(self) -> bool:
        """Whether to skip instruments during active high-impact news (default True)."""
        try:
            return bool(self._find_property(["market_source", "universe", "news_filter"]))
        except Exception:
            return True
    






    
