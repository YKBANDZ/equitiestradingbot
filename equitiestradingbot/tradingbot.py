import logging
import traceback
from datetime import datetime as dt
from pathlib import Path
from typing import List, Optional

import pytz

from .components import (
    Backtester,
    Configuration,
    MarketClosedException,
    MarketProvider,
    NotSafeToTradeException,
    TimeAmount,
    TimeProvider,
    TradeDirection,
)
from .components.broker.broker import Broker, BrokerFactory
from .interfaces import Market, Position
from .strategies import StrategyFactory, StrategyImp1
from .agent.journal.trade_journal import TradeJournal
from .agent.journal.analytics import JournalAnalytics
from .agent.reflection.reflection_engine import ReflectionEngine
from .agent.intelligence.intelligence_tools import IntelligenceTools
from .agent.intelligence.session import SessionTracker
from .agent.intelligence.calendar import EconomicCalendar
from .agent.instruments.universe import InstrumentUniverse
from .agent.instruments.session_provider import SessionAwareMarketProvider
from .agent.decision.decision_engine import AgentDecisionEngine, AgentDecision
from .agent.scheduler.bot_scheduler import BotScheduler


class TradingBot:
    """
    Class that indicates and hold references of main components like
    the broker interface, the strategy or the epic_ids list
    """

    time_provider: TimeProvider
    config: Configuration
    broker: Broker
    strategy: StrategyImp1
    market_provider: MarketProvider

    def __init__(
            self,
            time_provider: Optional[TimeProvider] = None,
            config_filepath: Optional[Path] = None,
    ) -> None:
        # Time manager
        self.time_provider = time_provider if time_provider else TimeProvider()
        # Set timeszone 
        set(pytz.all_timezones_set)

        # Load configuration
        self.config = Configuration.from_filepath(config_filepath)

        #Setup the global logger
        self.setup_logging()

        # Init trade services and create the broker interface
        # The Factory is used to create the services frm the configuration file
        self.broker = Broker(BrokerFactory(self.config))

        # Logging statement
        logging.info(f"Using stock interface: {type(self.broker.stocks_ifc).__name__}")

        # Create strategy from the factory class
        self.strategy = StrategyFactory(
            self.config, self.broker
        ).make_from_configuration()

        # Create the market provider
        # 'universe' mode: session-aware multi-instrument provider
        # All other modes: existing MarketProvider (list / watchlist / api)
        if self.config.get_active_market_source() == "universe":
            self._universe = InstrumentUniverse(
                path=self.config.get_universe_filepath(),
                analytics=None,  # analytics not yet initialised; injected below
            )
            self.market_provider = SessionAwareMarketProvider(
                universe=self._universe,
                broker=self.broker,
                news_filter=self.config.get_universe_news_filter(),
            )
            logging.info(
                "Market provider: SessionAwareMarketProvider — %s",
                self._universe.summary(),
            )
        else:
            self._universe = None
            self.market_provider = MarketProvider(self.config, self.broker)
            logging.info("Market provider: MarketProvider (mode=%s)", self.config.get_active_market_source())

        # Initialise the trade journal (creates DB + tables on first run)
        self.journal = TradeJournal()
        self.analytics = JournalAnalytics()
        logging.info("Trade journal initialised")

        # Now that analytics is available, inject it into the universe so
        # performance-weighted instrument ranking works from the first spin
        if self._universe is not None:
            self._universe._analytics = self.analytics

        # Initialise the intelligence layer (session, calendar, regime)
        self._intelligence = IntelligenceTools(self.broker)
        self._calendar = EconomicCalendar()
        logging.info("Intelligence layer initialised")

        # 24/7 Scheduler — only used in universe mode
        self._scheduler: Optional[BotScheduler] = None
        if self.config.get_active_market_source() == "universe":
            self._scheduler = BotScheduler(
                intervals=self.config.get_scheduler_intervals()
            )
            logging.info("BotScheduler initialised — 24/7 mode active")

        # Initialise the reflection engine (optional — disabled if no API key)
        self._reflection_engine: Optional[ReflectionEngine] = None
        self._closed_trade_count: int = 0
        self._last_daily_reflection_date: Optional[str] = None
        self._last_weekly_reflection_date: Optional[str] = None
        api_key = self.config.get_anthropic_api_key()
        if api_key:
            self._reflection_engine = ReflectionEngine(
                self.journal, self.analytics,
                api_key=api_key,
                intelligence_tools=self._intelligence,
            )
            logging.info("Reflection engine initialised")

            # Decision engine — used in universe mode to replace rule-based strategies
            self._decision_engine: Optional[AgentDecisionEngine] = None
            if self.config.get_active_market_source() == "universe":
                self._decision_engine = AgentDecisionEngine(
                    broker=self.broker,
                    journal=self.journal,
                    analytics=self.analytics,
                    api_key=api_key,
                )
                logging.info("AgentDecisionEngine initialised — AI trading mode active")
        else:
            self._decision_engine = None
            logging.warning(
                "No Anthropic API key found — AI decision engine disabled, "
                "falling back to rule-based strategy. "
                "Set ANTHROPIC_API_KEY or add agent.anthropic_credentials_filepath to config."
            )
    
    def setup_logging(self) -> None:
        """
        Setup the global logging settings
        """
        # Clean logging handlers
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)

        # Define the global logging settings
        debugLevel = (
            logging.DEBUG if self.config.is_logging_debug_enabled() else logging.INFO
        )
        if self.config.is_logging_enabled():
            log_filename = self.config.get_log_filepath()
            Path(log_filename).parent.mkdir(parents=True, exist_ok=True)
            logging.basicConfig(
                filename=log_filename,
                level=debugLevel,
                format="[%(asctime)s] %(levelname)s: %(message)s"
            )
        else:
            logging.basicConfig(
                level=debugLevel, format="[%(asctime)s] %(levelname)s: %(message)s"
            )
            
    def start(self, single_pass=False) -> None:
        """
        Main bot loop. Two operating modes:

        Universe mode (24/7):
          - Never stops due to market hours; sleeps intelligently between sessions
          - Scan frequency adapts to the current trading session
          - Weekends: sleeps from Friday 22:00 UTC → Sunday 22:00 UTC

        Legacy mode (rule-based):
          - Preserves original MarketClosedException / fixed spin_interval behaviour
        """
        if single_pass:
            logging.info("Performing a single iteration of the market source")

        if self._scheduler is not None:
            self._start_24_7(single_pass)
        else:
            self._start_legacy(single_pass)

    def _start_24_7(self, single_pass: bool) -> None:
        """24/7 loop used when market_source = 'universe'."""
        logging.info("Starting in 24/7 universe mode")
        while True:
            try:
                status = self._scheduler.get_status()
                logging.info(
                    "Scheduler: %s | session=%s | next_sleep=%s",
                    status["utc_time"], status["session"], status["sleep_human"],
                )

                if not self._scheduler.should_trade():
                    secs = self._scheduler.get_sleep_duration()
                    logging.info(
                        "Scheduler: markets closed — sleeping %s",
                        status["sleep_human"],
                    )
                    self.time_provider.wait_for(TimeAmount.SECONDS, secs)
                    if single_pass:
                        break
                    continue

                self.process_open_positions()
                self.process_market_source()
                self._check_scheduled_reflections()

                secs = self._scheduler.get_sleep_duration()
                self.time_provider.wait_for(TimeAmount.SECONDS, secs)

                if single_pass:
                    break

            except StopIteration:
                # Session queue exhausted — wait for next session
                secs = self._scheduler.get_sleep_duration()
                logging.info(
                    "Scheduler: session queue exhausted — sleeping %ds until next cycle",
                    secs,
                )
                if single_pass:
                    break
                self.time_provider.wait_for(TimeAmount.SECONDS, secs)

            except NotSafeToTradeException:
                logging.warning("Scheduler: not safe to trade — waiting one spin")
                if single_pass:
                    break
                self.time_provider.wait_for(
                    TimeAmount.SECONDS, self._scheduler.get_sleep_duration()
                )

            except KeyboardInterrupt:
                logging.info("Scheduler: Ctrl+C received — shutting down cleanly")
                break

            except Exception as e:
                logging.error("Scheduler: unhandled exception — %s", e)
                logging.error(traceback.format_exc())
                if single_pass:
                    break
                # Back off for one full spin before retrying
                self.time_provider.wait_for(
                    TimeAmount.SECONDS, self._scheduler.get_sleep_duration()
                )

    def _start_legacy(self, single_pass: bool) -> None:
        """Original fixed-interval loop for list/watchlist/api modes."""
        while True:
            try:
                self.process_open_positions()
                self.process_market_source()
                self._check_scheduled_reflections()
                self.time_provider.wait_for(
                    TimeAmount.SECONDS, self.config.get_spin_interval()
                )
                if single_pass:
                    break
            except KeyboardInterrupt:
                logging.info("Bot: Ctrl+C received — shutting down cleanly")
                break
            except MarketClosedException:
                logging.warning("Market is closed: stop processing")
                if single_pass:
                    break
                current_epic = self._get_current_epic()
                self.time_provider.wait_for(TimeAmount.NEXT_MARKET_OPENING, epic=current_epic)
            except NotSafeToTradeException:
                if single_pass:
                    break
                self.time_provider.wait_for(
                    TimeAmount.SECONDS, self.config.get_spin_interval()
                )
            except StopIteration:
                if single_pass:
                    break
                self.time_provider.wait_for(
                    TimeAmount.SECONDS, self.config.get_spin_interval()
                )
            except Exception as e:
                logging.error("Generic exception caught: {}".format(e))
                logging.error(traceback.format_exc())
                if single_pass:
                    break

    def process_open_positions(self) -> None:
        """
        Fetch open positions markets an run the strategy against them closing
        the trades if required
        """
        positions = self.broker.get_open_positions()
        # Do not run until we know the current open positions
        if positions is None:
            logging.warning("Unable to fetch open Positions! Will try again...")
            raise RuntimeError("Unable to fetch open positions")
        for epic in [item.epic for item in positions]:
            market = self.market_provider.get_market_from_epic(epic)
            self.process_market(market, positions)
    
    def process_market_source(self) -> None:
        """
        Process markets from the configured market source
        """
        logging.info("Starting to process market source")
        try:
            # First check if we have any open positions
            positions = self.broker.get_open_positions()
            if positions is None:
                logging.warning("Unable to fetch open positions! Will try again...")
                raise RuntimeError("Unable to fetch open positions")
            
            # If we have any open positions, don't look for new trades
            if len(positions) > 0:
                logging.info("Open positions exist. Skipping new trade search.")
                return
            
            # Only look for new trades if we have no open positions
            while True:
                market = self.market_provider.next()
                self.process_market(market, positions)
        except StopIteration:
            logging.info("Finished processing all markets in the source")
            # Reset the market provider to ensure fresh data on next iteration
            self.market_provider.reset()
            return
    
    def process_market(self, market: Market, open_positions: List[Position]) -> None:
        """
        Analyse a market and process any resulting trade signal.
        Routes to the AI decision engine (universe mode) or the legacy
        rule-based strategy depending on configuration.
        """
        if not self.config.is_paper_trading_enabled():
            self.safety_checks()
        logging.info("Processing %s", market.id)
        try:
            if self._decision_engine is not None:
                self._process_market_ai(market, open_positions)
            else:
                self._process_market_strategy(market, open_positions)
        except Exception as e:
            logging.error("Market processing exception: %s", e)
            logging.debug(traceback.format_exc())

    def _process_market_ai(self, market: Market, open_positions: List[Position]) -> None:
        """AI path — Claude decides BUY/SELL/HOLD via AgentDecisionEngine."""
        logging.info("AI decision: analysing %s (%s)", market.name, market.epic)
        decision: AgentDecision = self._decision_engine.decide(
            epic=market.epic,
            market_name=market.name,
        )

        if not decision.is_trade:
            logging.info("AI decision: HOLD %s — %s", market.epic, decision.reasoning[:100])
            return

        # Convert absolute price levels from decision to TradeDirection
        direction = TradeDirection.BUY if decision.action == "BUY" else TradeDirection.SELL

        # Log reasoning before executing the trade
        logging.info(
            "AI decision: %s %s  limit=%.4f  stop=%.4f  conf=%.2f  '%s'",
            decision.action, market.epic,
            decision.limit or 0, decision.stop or 0,
            decision.confidence, decision.reasoning[:120],
        )

        self.process_trade(
            market, direction, decision.limit, decision.stop,
            open_positions, reasoning=decision.reasoning,
        )

    def _process_market_strategy(self, market: Market, open_positions: List[Position]) -> None:
        """Legacy path — rule-based strategy decides."""
        self.strategy.set_open_positions(open_positions)
        trade, limit, stop = self.strategy.run(market)
        self.process_trade(market, trade, limit, stop, open_positions)
        
    def close_open_positions(self) -> None:
        """
        Closes all the positions in the account
        """
        logging.info("Closing all the open positions...")
        if self.broker.close_all_positions():
            logging.info("All the positions have been closed")
        else:
            logging.error("Impossible to close all open positions, retry.")

    
    def safety_checks(self) -> None:
        """
        Pre-trade safety gate.

        Universe mode  — checks account usage only.  Market-hour filtering is
                         handled by SessionAwareMarketProvider and BotScheduler.

        Legacy mode    — also checks whether the configured epic's market is open
                         (original behaviour preserved).
        """
        percent_used = self.broker.get_account_used_perc()
        if percent_used is None:
            logging.warning("Stop trading: account percentage used can't be fetched")
            raise NotSafeToTradeException()
        if percent_used >= self.config.get_max_account_usable():
            logging.warning(
                "Stop trading: %.1f%% of account used (max %s%%)",
                percent_used, self.config.get_max_account_usable(),
            )
            raise NotSafeToTradeException()

        # Universe mode: no hardcoded market-hours check — SessionAwareMarketProvider
        # only surfaces instruments that are active in the current session.
        if self._scheduler is not None:
            return

        # Legacy mode: keep original market-hours check
        if not self.config.is_paper_trading_enabled():
            with open(self.config.get_epic_ids_filepath(), "r") as f:
                epic = f.readline().strip()
            if not self.time_provider.is_market_open(self.config.get_time_zone(), epic):
                raise MarketClosedException()
        
    
    def process_trade(
            self,
            market: Market,
            direction: TradeDirection,
            limit: Optional[float],
            stop: Optional[float],
            open_positions: List[Position],
            reasoning: str = "",
    ) -> None:
        """
        Process a trade checking if it is a "close position" trade or a new trade.
        Journals every open and close event for the AI agent's memory layer.
        """
        # Perform trade only if required
        if direction is TradeDirection.NONE or limit is None or stop is None:
            return

        for item in open_positions:
            # If a same direction trade already exists, don't trade
            if item.epic == market.epic and direction is item.direction:
                logging.info(
                    "There is already an open position for the epic, skip trade"
                )
                return
            # If a trade in opposite direction exists, close the position
            elif item.epic == market.epic and direction is not item.direction:
                if self.broker.close_position(item):
                    self._journal_close(item, market)
                return

        if self.broker.trade(market.epic, direction, limit, stop):
            self._journal_open(market, direction, limit, stop, reasoning=reasoning)

    def _journal_open(
        self,
        market: Market,
        direction: TradeDirection,
        limit: float,
        stop: float,
        reasoning: str = "",
    ) -> None:
        """
        Log a newly opened trade, enriched with session, regime, volatility,
        and news context from the intelligence layer.
        """
        try:
            positions = self.broker.get_open_positions() or []
            matched = next(
                (p for p in positions
                 if p.epic == market.epic and p.direction == direction),
                None,
            )
            deal_id     = matched.deal_id if matched else f"UNKNOWN-{market.epic}-{direction.value}"
            entry_price = matched.level   if matched else market.offer
            size        = matched.size    if matched else 0
            stop_level  = matched.stop    if matched else stop
            limit_level = matched.limit   if matched else limit

            # ── Intelligence enrichment ──────────────────────────────────────
            session     = SessionTracker.get_current_session()
            news_active = self._calendar.is_high_impact_active(buffer_minutes=30)

            # Regime + ATR (best-effort — don't let failure block journalling)
            regime    = ""
            atr_value = None
            try:
                ctx = self._intelligence.get_market_regime(
                    market.epic, interval="HOUR", bars=250
                )
                regime    = ctx.get("regime", "")
                atr_value = ctx.get("atr_14")
            except Exception as ie:
                logging.debug("Journal: regime fetch skipped — %s", ie)

            self.journal.log_trade(
                deal_id=deal_id,
                epic=market.epic,
                direction=direction.value,
                entry_price=entry_price,
                size=size,
                market_name=market.name,
                limit_level=limit_level,
                stop_level=stop_level,
                strategy_used=type(self.strategy).__name__,
                session=session,
                market_regime=regime,
                volatility_at_entry=atr_value,
                news_active=news_active,
                reasoning=reasoning,
            )
            logging.info(
                "Journal: trade opened — %s %s @ %.4f  session=%s  regime=%s  news=%s",
                direction.value, market.epic, entry_price, session, regime, news_active,
            )
        except Exception as e:
            logging.warning("Journal: failed to log open trade — %s", e)

    def _journal_close(self, position: Position, market: Market) -> None:
        """
        Log the outcome of a closed position.
        Approximates exit price from the current market bid/offer.
        """
        try:
            # For a BUY we exit at bid; for a SELL we exit at offer
            exit_price = (
                market.bid
                if position.direction is TradeDirection.BUY
                else market.offer
            )
            direction_sign = 1 if position.direction is TradeDirection.BUY else -1
            pnl = round((exit_price - position.level) * position.size * direction_sign, 2)
            pnl_pct = round(pnl / (position.level * position.size), 4) if position.level and position.size else None

            closed_trade = self.journal.log_outcome(
                deal_id=position.deal_id,
                exit_price=exit_price,
                pnl=pnl,
                pnl_pct=pnl_pct,
            )
            logging.info(
                "Journal: trade closed — %s  exit=%.4f  pnl=%.2f",
                position.deal_id, exit_price, pnl,
            )
            self._closed_trade_count += 1
            self._trigger_post_trade_reflection(closed_trade)
        except Exception as e:
            logging.warning("Journal: failed to log close — %s", e)

    def backtest(
            self,
            market_id: str,
            start_date: str,
            end_date: str,
            epic_id: Optional[str] = None,
    ) -> None:
        """
        Backtest a market using the configured strategy
        """
        try:
            start = dt.strptime(start_date, "%Y-%m-%d")
            end = dt.strptime(end_date, "%Y-%m-%d")
        except ValueError as e:
            logging.error("Wrong date format! Must be YYYY-MM-DD")
            logging.debug(e)
            exit(1)

        bt = Backtester(self.broker, self.strategy)

        try:
            market = (
                self.market_provider.search_market(market_id)
                if epic_id is None or epic_id == ""
                else self.market_provider.get_market_from_epic(epic_id)
            )
        except Exception as e:
            logging.error(e)
            exit(1)
        
        bt.start(market, start, end)
        bt.print_results()

    
    def _trigger_post_trade_reflection(self, trade: dict) -> None:
        """
        Fire a post-trade reflection after every N closed trades if configured.
        Runs in the same thread — kept fast by the post-trade prompt design.
        """
        if not self._reflection_engine:
            return
        n = self.config.get_post_trade_reflection_n()
        if n <= 0:
            return
        if self._closed_trade_count % n == 0:
            try:
                logging.info("Reflection: triggering post-trade review")
                self._reflection_engine.reflect_on_trade(trade)
            except Exception as e:
                logging.error("Reflection: post-trade review failed — %s", e)

    def _check_scheduled_reflections(self) -> None:
        """
        Called once per main loop spin.  Triggers daily and weekly reflections
        when the configured UTC hour is reached and they haven't run yet today.
        """
        if not self._reflection_engine:
            return

        now_utc = dt.utcnow()
        today_str = now_utc.strftime("%Y-%m-%d")
        current_hour = now_utc.hour

        # Daily reflection
        daily_hour = self.config.get_daily_reflection_hour_utc()
        if (
            daily_hour >= 0
            and current_hour == daily_hour
            and self._last_daily_reflection_date != today_str
        ):
            try:
                logging.info("Reflection: triggering daily review for %s", today_str)
                self._reflection_engine.run_daily_reflection(today_str)
                self._last_daily_reflection_date = today_str
            except Exception as e:
                logging.error("Reflection: daily review failed — %s", e)

        # Weekly reflection
        weekly_day  = self.config.get_weekly_reflection_day()
        weekly_hour = self.config.get_weekly_reflection_hour_utc()
        if (
            weekly_day >= 0
            and now_utc.weekday() == weekly_day
            and current_hour == weekly_hour
            and self._last_weekly_reflection_date != today_str
        ):
            try:
                logging.info("Reflection: triggering weekly review")
                self._reflection_engine.run_weekly_reflection()
                self._last_weekly_reflection_date = today_str
            except Exception as e:
                logging.error("Reflection: weekly review failed — %s", e)

    def _get_current_epic(self) -> str:
        """Get the current epic being traded"""
        try:
            # Try to get epic from the market source
            with open(self.config.get_epic_ids_filepath(), 'r') as f:
                epic = f.readline().strip()
                return epic
        except Exception:
            # Default to Gold futures if we can't read the file
            return "CS.D.USCGC.TODAY.IP"
        
        