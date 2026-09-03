import sqlite3
import os

DB_NAME = 'activos_data.db'
DB_PATH = '/home/soporte/ochoa/activos_data.db'

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    
    # 1. Create assets table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            type TEXT NOT NULL, -- 'stock' or 'crypto'
            currency TEXT DEFAULT 'USD',
            last_price REAL,
            change_percent REAL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 2. Create news_analysis table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS news_analysis (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            headline TEXT UNIQUE NOT NULL,
            url TEXT,
            sentiment TEXT NOT NULL, -- 'positivo', 'negativo', 'neutral', 'insider_sell', 'insider_buy', 'regulatory'
            impact_score INTEGER NOT NULL, -- -100 to 100
            source TEXT,
            published_at TIMESTAMP,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            price_at_news REAL,
            price_1h_later REAL,
            price_4h_later REAL,
            price_24h_later REAL,
            FOREIGN KEY (asset_id) REFERENCES assets(id)
        )
    ''')
    
    # 3. Create price_history table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset_id INTEGER NOT NULL,
            price REAL NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (asset_id) REFERENCES assets(id)
        )
    ''')
    
    conn.commit()
    
    # Check if empty, prepopulate with default assets
    cursor.execute("SELECT COUNT(*) FROM assets")
    if cursor.fetchone()[0] == 0:
        default_assets = [
            ("ASTS", "AST SpaceMobile", "stock"),
            ("TSLA", "Tesla", "stock"),
            ("AAPL", "Apple", "stock"),
            ("NVDA", "NVIDIA", "stock"),
            ("BTC-USD", "Bitcoin", "crypto"),
            ("ETH-USD", "Ethereum", "crypto"),
            ("SOL-USD", "Solana", "crypto")
        ]
        cursor.executemany('''
            INSERT INTO assets (symbol, name, type) VALUES (?, ?, ?)
        ''', default_assets)
        conn.commit()
        print("🌱 Base de datos inicializada y poblada con activos por defecto.")
    else:
        print("✅ Base de datos ya configurada.")
        
    conn.close()

if __name__ == '__main__':
    init_db()
