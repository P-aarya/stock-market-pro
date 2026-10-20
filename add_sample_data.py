"""add_sample_data.py - puts back the July sample portfolio:
10 holdings and 20 transactions.

Run once from your project folder:   python add_sample_data.py

Note: it empties the portfolio and transactions tables first, so running it
again just resets them to this same sample data.
"""
import os
import mysql.connector
from dotenv import load_dotenv

load_dotenv()


def get_db():
    config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", os.getenv("DB_PASS", "")),
        "database": os.getenv("DB_NAME", "stock_market_pro_db"),
    }
    if os.getenv("DB_SSL", "false").lower() == "true":
        config["ssl_disabled"] = False
    return mysql.connector.connect(**config)


HOLDINGS = [
    # ticker, company_name, quantity, avg_buy_price, buy_date, sector, notes
    ("NVDA",  "NVIDIA Corporation",     10, 180.50, "2024-01-15", "Semiconductors",        "Long term AI play"),
    ("AAPL",  "Apple Inc.",             20, 165.20, "2024-02-10", "Technology",            "Core holding"),
    ("MSFT",  "Microsoft Corporation",  15, 380.00, "2024-01-20", "Technology",            "Cloud growth"),
    ("AMZN",  "Amazon.com Inc.",        12, 195.75, "2024-03-05", "Cloud & SaaS",          "E-commerce + AWS"),
    ("GOOGL", "Alphabet Inc.",          10, 160.40, "2024-02-28", "Technology",            "Ad revenue recovery"),
    ("JPM",   "JPMorgan Chase & Co.",   25, 185.00, "2024-01-10", "Banking",               "Financials exposure"),
    ("JNJ",   "Johnson & Johnson",      18, 155.30, "2024-03-15", "Healthcare",            "Defensive holding"),
    ("TSLA",  "Tesla Inc.",              8, 220.00, "2024-04-01", "Automotive",            "EV sector bet"),
    ("META",  "Meta Platforms Inc.",    12, 480.00, "2024-02-15", "Media & Entertainment", "AI + social"),
    ("V",     "Visa Inc.",              15, 270.00, "2024-01-25", "Financials",            "Payment network"),
]

TRANSACTIONS = [
    # ticker, company_name, type, quantity, price, date, notes
    ("NVDA",  "NVIDIA Corporation",    "BUY",  10, 180.50, "2024-01-15", "Initial position"),
    ("AAPL",  "Apple Inc.",            "BUY",  20, 165.20, "2024-02-10", "Core holding"),
    ("MSFT",  "Microsoft Corporation", "BUY",  15, 380.00, "2024-01-20", "Cloud thesis"),
    ("AMZN",  "Amazon.com Inc.",       "BUY",  12, 195.75, "2024-03-05", "AWS growth"),
    ("GOOGL", "Alphabet Inc.",         "BUY",  10, 160.40, "2024-02-28", "Value entry"),
    ("JPM",   "JPMorgan Chase",        "BUY",  25, 185.00, "2024-01-10", "Financials"),
    ("JNJ",   "Johnson & Johnson",     "BUY",  18, 155.30, "2024-03-15", "Defensive"),
    ("TSLA",  "Tesla Inc.",            "BUY",   8, 220.00, "2024-04-01", "EV exposure"),
    ("META",  "Meta Platforms",        "BUY",  12, 480.00, "2024-02-15", "AI pivot"),
    ("V",     "Visa Inc.",             "BUY",  15, 270.00, "2024-01-25", "Payments"),
    ("TSLA",  "Tesla Inc.",            "SELL",  5, 265.00, "2024-05-10", "Partial profit"),
    ("AAPL",  "Apple Inc.",            "SELL",  5, 185.00, "2024-04-20", "Rebalancing"),
    ("NVDA",  "NVIDIA Corporation",    "SELL",  3, 420.00, "2024-06-01", "Taking profits"),
    ("NVDA",  "NVIDIA Corporation",    "BUY",   3, 850.00, "2024-09-15", "Adding back"),
    ("META",  "Meta Platforms",        "BUY",   5, 520.00, "2024-10-01", "Earnings play"),
    ("MSFT",  "Microsoft Corporation", "BUY",   5, 410.00, "2024-11-20", "Copilot growth"),
    ("AMZN",  "Amazon.com Inc.",       "BUY",   8, 210.00, "2025-01-10", "Adding position"),
    ("GOOGL", "Alphabet Inc.",         "BUY",   5, 175.00, "2025-02-14", "Gemini AI"),
    ("JPM",   "JPMorgan Chase",        "SELL", 10, 220.00, "2025-03-01", "Rebalancing"),
    ("V",     "Visa Inc.",             "BUY",   5, 285.00, "2025-04-15", "Adding payments"),
]


def main():
    conn = get_db()
    cur = conn.cursor()
    print("Connected to", os.getenv("DB_HOST", "localhost"))

    cur.execute("DELETE FROM portfolio")
    cur.execute("DELETE FROM transactions")

    cur.executemany(
        """INSERT INTO portfolio
           (ticker, company_name, quantity, avg_buy_price, buy_date, sector, notes)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        HOLDINGS,
    )
    cur.executemany(
        """INSERT INTO transactions
           (ticker, company_name, transaction_type, quantity, price, total_value, transaction_date, notes)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
        [(t, n, ty, q, p, round(q * p, 2), d, note) for (t, n, ty, q, p, d, note) in TRANSACTIONS],
    )
    conn.commit()

    cur.execute("SELECT COUNT(*) FROM portfolio")
    n_hold = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM transactions")
    n_tx = cur.fetchone()[0]
    cur.close()
    conn.close()
    print(f"Portfolio holdings in database: {n_hold}")
    print(f"Transactions in database:       {n_tx}")
    print("Done. Restart api.py and open the Portfolio tab.")


if __name__ == "__main__":
    main()
