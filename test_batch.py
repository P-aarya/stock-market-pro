from dotenv import load_dotenv
load_dotenv()
import os
print('DB_HOST:', os.getenv('DB_HOST'))
print('DB_PORT:', os.getenv('DB_PORT'))
import mysql.connector
try:
    conn = mysql.connector.connect(
        host=os.getenv('DB_HOST'),
        port=int(os.getenv('DB_PORT', 3306)),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        database=os.getenv('DB_NAME'),
        ssl_disabled=False
    )
    cursor = conn.cursor()
    cursor.execute("SELECT ticker, company_name FROM stocks_live WHERE ticker IN ('NEM','XOM','ADM') LIMIT 3")
    print('Results:', cursor.fetchall())
    conn.close()
except Exception as e:
    print('Error:', e)
