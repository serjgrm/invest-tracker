import sqlite3

DB_PATH = "invest.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            buy_date TEXT NOT NULL,      -- YYYY-MM-DD
            buy_price REAL NOT NULL,
            qty REAL NOT NULL DEFAULT 1
        )
        """)
        conn.commit()
