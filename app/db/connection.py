import sqlite3
from pathlib import Path

from app.db.migrations import migrate

DEFAULT_DB_PATH = Path("data") / "veille.db"


def connect(path: str | Path = DEFAULT_DB_PATH, check_same_thread: bool = True) -> sqlite3.Connection:
    """Ouvre la base (la crée si besoin) et applique les migrations."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    migrate(conn)
    return conn
