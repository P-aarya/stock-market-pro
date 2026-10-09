import os
from dotenv import load_dotenv
import mysql.connector

load_dotenv()


def get_connection():
    config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "stock_market_pro_db"),
    }
    if os.getenv("DB_SSL", "false").lower() == "true":
        config["ssl_disabled"] = False
    return mysql.connector.connect(**config)


def get_db_tickers():
    """Every ticker already in stocks_live (empty list if the database is unreachable)."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ticker FROM stocks_live")
        rows = [r[0] for r in cursor.fetchall()]
        cursor.close(); conn.close()
        return rows
    except Exception as e:
        print(f"Could not read tickers from the database: {e}")
        return []


def get_db_sectors():
    """{ticker: sector} for stocks already in stocks_live."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ticker, sector FROM stocks_live WHERE sector IS NOT NULL")
        rows = {r[0]: r[1] for r in cursor.fetchall()}
        cursor.close(); conn.close()
        return rows
    except Exception as e:
        print(f"Could not read sectors from the database: {e}")
        return {}
