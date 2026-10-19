"""
Fills ONE account with sample holdings and transactions, replacing whatever that account had.
Other accounts are never touched.

    python add_sample_data.py YOU@EXAMPLE.COM
"""
import sys

import mysql.connector
import db

def get_db():
    return db.get_connection()

def main(email):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE email = %s", (email.strip().lower(),))
    row = cursor.fetchone()
    if not row:
        sys.exit(f"No account found for {email}. Create it first: python manage_users.py create-user {email}")
    user_id = row[0]
    cursor.execute("DELETE FROM portfolio WHERE user_id = %s", (user_id,))
    cursor.execute("DELETE FROM transactions WHERE user_id = %s", (user_id,))
    conn.commit()

    holdings = [
        ("NVDA", "NVIDIA Corporation",        10,  180.50, "2024-01-15", "Semiconductors",        "Long term AI play"),
        ("AAPL", "Apple Inc.",                 20,  165.20, "2024-02-10", "Technology",            "Core holding"),
        ("MSFT", "Microsoft Corporation",      15,  380.00, "2024-01-20", "Technology",            "Cloud growth"),
        ("AMZN", "Amazon.com Inc.",            12,  195.75, "2024-03-05", "Cloud & SaaS",          "E-commerce + AWS"),
        ("GOOGL","Alphabet Inc.",              10,  160.40, "2024-02-28", "Technology",            "Ad revenue recovery"),
        ("JPM",  "JPMorgan Chase & Co.",       25,  185.00, "2024-01-10", "Banking",               "Financials exposure"),
        ("JNJ",  "Johnson & Johnson",          18,  155.30, "2024-03-15", "Healthcare",            "Defensive holding"),
        ("TSLA", "Tesla Inc.",                  8,  220.00, "2024-04-01", "Automotive",            "EV sector bet"),
        ("META", "Meta Platforms Inc.",        12,  480.00, "2024-02-15", "Media & Entertainment", "AI + social"),
        ("V",    "Visa Inc.",                  15,  270.00, "2024-01-25", "Financials",            "Payment network"),
    ]

    for h in holdings:
        cursor.execute("""INSERT INTO portfolio 
            (user_id,ticker,company_name,quantity,avg_buy_price,buy_date,sector,notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (user_id, *h))
    conn.commit()
    print(f"✅ Added {len(holdings)} portfolio holdings")

    transactions = [
        ("NVDA", "NVIDIA Corporation",    "BUY",  10, 180.50, "2024-01-15", "Initial position"),
        ("AAPL", "Apple Inc.",            "BUY",  20, 165.20, "2024-02-10", "Core holding"),
        ("MSFT", "Microsoft Corporation", "BUY",  15, 380.00, "2024-01-20", "Cloud thesis"),
        ("AMZN", "Amazon.com Inc.",       "BUY",  12, 195.75, "2024-03-05", "AWS growth"),
        ("GOOGL","Alphabet Inc.",         "BUY",  10, 160.40, "2024-02-28", "Value entry"),
        ("JPM",  "JPMorgan Chase",        "BUY",  25, 185.00, "2024-01-10", "Financials"),
        ("JNJ",  "Johnson & Johnson",     "BUY",  18, 155.30, "2024-03-15", "Defensive"),
        ("TSLA", "Tesla Inc.",            "BUY",   8, 220.00, "2024-04-01", "EV exposure"),
        ("META", "Meta Platforms",        "BUY",  12, 480.00, "2024-02-15", "AI pivot"),
        ("V",    "Visa Inc.",             "BUY",  15, 270.00, "2024-01-25", "Payments"),
        ("TSLA", "Tesla Inc.",            "SELL",  5, 265.00, "2024-05-10", "Partial profit"),
        ("AAPL", "Apple Inc.",            "SELL",  5, 185.00, "2024-04-20", "Rebalancing"),
        ("NVDA", "NVIDIA Corporation",    "SELL",  3, 420.00, "2024-06-01", "Taking profits"),
        ("NVDA", "NVIDIA Corporation",    "BUY",   3, 850.00, "2024-09-15", "Adding back"),
        ("META", "Meta Platforms",        "BUY",   5, 520.00, "2024-10-01", "Earnings play"),
        ("MSFT", "Microsoft Corporation", "BUY",   5, 410.00, "2024-11-20", "Copilot growth"),
        ("AMZN", "Amazon.com Inc.",       "BUY",   8, 210.00, "2025-01-10", "Adding position"),
        ("GOOGL","Alphabet Inc.",         "BUY",   5, 175.00, "2025-02-14", "Gemini AI"),
        ("JPM",  "JPMorgan Chase",        "SELL", 10, 220.00, "2025-03-01", "Rebalancing"),
        ("V",    "Visa Inc.",             "BUY",   5, 285.00, "2025-04-15", "Adding payments"),
    ]

    for t in transactions:
        ticker,name,ttype,qty,price,tdate,notes = t
        cursor.execute("""INSERT INTO transactions
            (user_id,ticker,company_name,transaction_type,quantity,price,total_value,transaction_date,notes)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (user_id,ticker,name,ttype,qty,price,round(qty*price,2),tdate,notes))
    conn.commit()
    print(f"✅ Added {len(transactions)} transactions")
    cursor.close(); conn.close()
    print("\n✅ Done! Refresh dashboard to see Portfolio and Transactions pages.")

if __name__=="__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python add_sample_data.py YOU@EXAMPLE.COM   (the account to fill with sample data)")
    main(sys.argv[1])
