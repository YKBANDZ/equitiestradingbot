"""
SQLite schema and connection manager for the trade journal.
All tables are created here; everything else imports get_connection().
"""

import sqlite3
from pathlib import Path
from contextlib import contextmanager
from typing import Generator

# Default location alongside the project config directory
DEFAULT_DB_PATH = (
    Path(__file__).resolve().parents[4] / "data" / "journal.db"
)

_db_path: Path = DEFAULT_DB_PATH


def set_db_path(path: Path) -> None:
    global _db_path
    _db_path = Path(path)


def get_db_path() -> Path:
    return _db_path


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    """Yield a connection with row_factory and WAL mode enabled."""
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path), detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except BaseException:
        # Catches both Exception and KeyboardInterrupt so the rollback
        # is always explicit rather than relying on SQLite's implicit
        # behaviour when the connection is closed mid-transaction.
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────

_SCHEMA = """
-- ── trades ──────────────────────────────────────────────────────────────────
-- One row per position opened.  Outcome fields are NULL until position closes.
CREATE TABLE IF NOT EXISTS trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    deal_id             TEXT    UNIQUE,          -- IG deal reference
    epic                TEXT    NOT NULL,
    market_name         TEXT,
    direction           TEXT    NOT NULL,        -- BUY | SELL
    entry_price         REAL    NOT NULL,
    entry_time          TEXT    NOT NULL,        -- ISO-8601
    exit_price          REAL,
    exit_time           TEXT,
    size                REAL    NOT NULL,
    limit_level         REAL,
    stop_level          REAL,
    pnl                 REAL,                   -- absolute profit/loss in account currency
    pnl_pct             REAL,                   -- % return on margin used
    status              TEXT    NOT NULL DEFAULT 'OPEN',  -- OPEN | CLOSED | CANCELLED
    strategy_used       TEXT,
    market_regime       TEXT,                   -- TRENDING_UP | TRENDING_DOWN | RANGING | VOLATILE
    session             TEXT,                   -- ASIAN | LONDON | NY | OVERLAP
    volatility_at_entry REAL,                   -- ATR or % range at entry
    news_active         INTEGER DEFAULT 0,      -- 1 if high-impact news was active at entry
    reasoning           TEXT,                   -- Claude's entry reasoning (free text)
    outcome_reflection  TEXT,                   -- Claude's post-close reflection
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- ── learnings ────────────────────────────────────────────────────────────────
-- Distilled lessons written by Claude after reviewing trade batches.
CREATE TABLE IF NOT EXISTS learnings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    category        TEXT    NOT NULL,  -- timing | risk | entry | exit | instrument | regime
    learning_text   TEXT    NOT NULL,
    confidence      REAL    NOT NULL DEFAULT 0.5,  -- 0.0–1.0
    trade_ids       TEXT,              -- JSON array of related trade IDs e.g. "[1,2,5]"
    superseded_by   INTEGER REFERENCES learnings(id)  -- links to newer version
);

-- ── reflections ──────────────────────────────────────────────────────────────
-- Periodic (daily / batch) reflection sessions Claude runs over performance.
CREATE TABLE IF NOT EXISTS reflections (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    period_start    TEXT    NOT NULL,  -- ISO-8601 date range covered
    period_end      TEXT    NOT NULL,
    trades_reviewed INTEGER NOT NULL DEFAULT 0,
    win_rate        REAL,
    total_pnl       REAL,
    summary         TEXT    NOT NULL,  -- Claude's narrative summary
    action_items    TEXT               -- JSON array of strings
);

-- ── strategy_preferences ────────────────────────────────────────────────────
-- Claude's living rulebook.  Only one active row at a time.
CREATE TABLE IF NOT EXISTS strategy_preferences (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    updated_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    version     INTEGER NOT NULL DEFAULT 1,
    is_active   INTEGER NOT NULL DEFAULT 1,  -- only 1 row has is_active=1
    preferences TEXT    NOT NULL             -- full JSON blob
);

-- ── performance_snapshots ────────────────────────────────────────────────────
-- Lightweight daily/weekly stats roll-ups for quick context loading.
CREATE TABLE IF NOT EXISTS performance_snapshots (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_time   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    period          TEXT    NOT NULL,   -- DAILY | WEEKLY | MONTHLY
    total_trades    INTEGER NOT NULL DEFAULT 0,
    winning_trades  INTEGER NOT NULL DEFAULT 0,
    losing_trades   INTEGER NOT NULL DEFAULT 0,
    win_rate        REAL,
    total_pnl       REAL,
    avg_pnl         REAL,
    max_drawdown    REAL,
    best_instrument TEXT,
    worst_instrument TEXT,
    notes           TEXT
);

-- ── indexes ──────────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_trades_epic       ON trades(epic);
CREATE INDEX IF NOT EXISTS idx_trades_status     ON trades(status);
CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time);
CREATE INDEX IF NOT EXISTS idx_trades_strategy   ON trades(strategy_used);
CREATE INDEX IF NOT EXISTS idx_learnings_cat     ON learnings(category);
"""


def initialise_db() -> None:
    """Create all tables if they don't already exist."""
    with get_connection() as conn:
        conn.executescript(_SCHEMA)
