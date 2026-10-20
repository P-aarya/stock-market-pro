from dotenv import load_dotenv
load_dotenv()
import os, mysql.connector

conn = mysql.connector.connect(
    host=os.getenv("DB_HOST"),
    port=int(os.getenv("DB_PORT", 3306)),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME"),
    ssl_disabled=False
)
cursor = conn.cursor()

cursor.execute("""
    CREATE TABLE IF NOT EXISTS price_alerts (
        id INT AUTO_INCREMENT PRIMARY KEY,
        ticker VARCHAR(10) NOT NULL,
        company_name VARCHAR(100),
        target_price DECIMAL(12,4) NOT NULL,
        condition_type ENUM('above','below') NOT NULL,
        current_price_at_creation DECIMAL(12,4),
        is_triggered BOOLEAN DEFAULT FALSE,
        triggered_at DATETIME NULL,
        triggered_price DECIMAL(12,4) NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        is_active BOOLEAN DEFAULT TRUE
    )
""")
conn.commit()
print("✅ price_alerts table created successfully")
cursor.close()
conn.close()
