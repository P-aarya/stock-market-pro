# fix_predict_tables.py
# Run this ONCE to add missing columns, then run predict_aarya.py normally
import os
from dotenv import load_dotenv
load_dotenv()
import mysql.connector

def get_db():
    config = {
        "host": os.getenv("DB_HOST"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "user": os.getenv("DB_USER"),
        "password": os.getenv("DB_PASSWORD"),
        "database": os.getenv("DB_NAME"),
        "ssl_disabled": False
    }
    return mysql.connector.connect(**config)

def add_column_if_missing(cursor, table, column, col_type):
    try:
        cursor.execute(f"SELECT {column} FROM {table} LIMIT 1")
        print(f"  OK  {table}.{column} already exists")
    except mysql.connector.Error:
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            print(f"  ADD {table}.{column}")
        except Exception as e:
            print(f"  ERR {table}.{column}: {e}")

def main():
    conn = get_db()
    cursor = conn.cursor()

    cols = [
        ("stocks_predictions", "upper_1w",              "FLOAT"),
        ("stocks_predictions", "lower_1w",              "FLOAT"),
        ("stocks_predictions", "upper_1m",              "FLOAT"),
        ("stocks_predictions", "lower_1m",              "FLOAT"),
        ("stocks_predictions", "upper_3m",              "FLOAT"),
        ("stocks_predictions", "lower_3m",              "FLOAT"),
        ("stocks_predictions", "prob_up_1w",            "FLOAT"),
        ("stocks_predictions", "prob_up_1m",            "FLOAT"),
        ("stocks_predictions", "prob_up_3m",            "FLOAT"),
        ("stocks_predictions", "key_factors",           "JSON"),
        ("stocks_predictions", "model_used",            "VARCHAR(50)"),
        ("stocks_predictions", "price_history",         "JSON"),
        ("stocks_predictions", "predicted_path",        "JSON"),
        ("stocks_predictions", "run_date",              "DATE"),
        ("stocks_predictions", "actual_1w",             "FLOAT"),
        ("stocks_predictions", "actual_1w_date",        "DATE"),
        ("stocks_predictions", "direction_correct_1w",  "TINYINT"),
    ]

    print("Checking/adding columns to stocks_predictions...\n")
    for table, col, dtype in cols:
        add_column_if_missing(cursor, table, col, dtype)

    conn.commit()
    cursor.close()
    conn.close()
    print("\nDone. Now run: python predict_aarya.py")

if __name__ == "__main__":
    main()
