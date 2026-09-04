import aiosqlite
import os

DB_PATH = os.getenv("OCHOA_DB_PATH", "ochoa.db")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                name TEXT
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                exchange TEXT NOT NULL,
                api_key TEXT NOT NULL,
                api_secret TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                state_file TEXT,
                risk_level TEXT DEFAULT 'Medio',
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        # Añadir columna si no existe (para bases de datos antiguas)
        try:
            await db.execute("ALTER TABLE bots ADD COLUMN risk_level TEXT DEFAULT 'Medio'")
        except aiosqlite.OperationalError:
            pass # Ya existe la columna
            
        try:
            await db.execute("ALTER TABLE bots ADD COLUMN smart_dca_enabled BOOLEAN DEFAULT 0")
        except aiosqlite.OperationalError:
            pass
            
        try:
            await db.execute("ALTER TABLE bots ADD COLUMN smart_dca_bullets INTEGER DEFAULT 3")
        except aiosqlite.OperationalError:
            pass
            
        try:
            await db.execute("ALTER TABLE bots ADD COLUMN base_trade_amount REAL DEFAULT 2.0")
        except aiosqlite.OperationalError:
            pass
            
        try:
            await db.execute("ALTER TABLE bots ADD COLUMN bot_name TEXT DEFAULT 'Alpaca Bot'")
        except aiosqlite.OperationalError:
            pass
            
        try:
            await db.execute("ALTER TABLE bots ADD COLUMN portfolio_id TEXT")
        except aiosqlite.OperationalError:
            pass
        await db.commit()

async def get_or_create_user(email: str, name: str = ""):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT id, email, name FROM users WHERE email = ?", (email,)) as cursor:
            user = await cursor.fetchone()
            if user:
                return dict(id=user[0], email=user[1], name=user[2])
            
        await db.execute("INSERT INTO users (email, name) VALUES (?, ?)", (email, name))
        await db.commit()
        
        async with db.execute("SELECT id, email, name FROM users WHERE email = ?", (email,)) as cursor:
            user = await cursor.fetchone()
            return dict(id=user[0], email=user[1], name=user[2])

async def get_user_bots(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bots WHERE user_id = ?", (user_id,)) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def add_bot(user_id: int, exchange: str, api_key: str, api_secret: str, bot_name: str = "Alpaca Bot", is_live: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        # Insert the bot directly without checking for duplicate API keys 
        # so the user can have multiple bots sharing the same credentials
        cursor = await db.execute(
            "INSERT INTO bots (user_id, exchange, api_key, api_secret, bot_name, is_live) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, exchange, api_key, api_secret, bot_name, is_live)
        )
        bot_id = cursor.lastrowid
        state_file = f"trader_state_user_{user_id}_bot_{bot_id}.json"
        await db.execute("UPDATE bots SET state_file = ? WHERE id = ?", (state_file, bot_id))
        await db.commit()

async def get_all_active_bots():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bots WHERE is_active = 1") as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def get_bot_by_id(bot_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bots WHERE id = ? AND user_id = ?", (bot_id, user_id)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

async def update_bot_risk(bot_id: int, user_id: int, risk_level: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE bots SET risk_level = ? WHERE id = ? AND user_id = ?",
            (risk_level, bot_id, user_id)
        )
        await db.commit()

async def update_bot_settings(bot_id: int, user_id: int, smart_dca_enabled: bool, smart_dca_bullets: int, base_trade_amount: float, portfolio_id: str = None, day_trader: bool = False, overdrive: bool = False, radar_size: int = 6, asset_universe: str = 'SP500', buy_threshold: int = 75, sell_threshold: int = 25, take_profit_pct: float = 100.0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE bots SET smart_dca_enabled = ?, smart_dca_bullets = ?, base_trade_amount = ?, portfolio_id = ?, day_trader = ?, overdrive = ?, radar_size = ?, asset_universe = ?, buy_threshold = ?, sell_threshold = ? WHERE id = ? AND user_id = ?",
            (smart_dca_enabled, smart_dca_bullets, base_trade_amount, portfolio_id, day_trader, overdrive, radar_size, asset_universe, buy_threshold, sell_threshold, bot_id, user_id)
        )
        await db.commit()

async def get_global_metrics():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        async with db.execute("SELECT COUNT(*) as total_users FROM users") as cursor:
            total_users = (await cursor.fetchone())['total_users']
            
        async with db.execute("SELECT COUNT(*) as total_bots FROM bots") as cursor:
            total_bots = (await cursor.fetchone())['total_bots']
            
        async with db.execute("SELECT COUNT(*) as active_bots FROM bots WHERE is_active = 1") as cursor:
            active_bots = (await cursor.fetchone())['active_bots']
            
        return {
            "total_users": total_users,
            "total_bots": total_bots,
            "active_bots": active_bots
        }

async def get_recent_news(limit=10):
    db_path = os.getenv("ACTIVOS_DB_PATH", os.path.join(os.path.dirname(__file__), "activos_data.db"))
    if not os.path.exists(db_path):
        return 0, []
        
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        
        async with db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='news_analysis'") as cursor:
            if (await cursor.fetchone())[0] == 0:
                return 0, []

        async with db.execute("SELECT COUNT(*) as total FROM news_analysis") as cursor:
            total_news = (await cursor.fetchone())['total']
            
        query = '''
            SELECT a.symbol, n.headline, n.sentiment, n.impact_score 
            FROM news_analysis n
            JOIN assets a ON n.asset_id = a.id
            WHERE a.type IN ('stock', 'index')
            ORDER BY n.id DESC LIMIT ?
        '''
        async with db.execute(query, (limit,)) as cursor:
            rows = await cursor.fetchall()
            news = [dict(row) for row in rows]
            
        return total_news, news

async def get_all_news():
    db_path = os.getenv("ACTIVOS_DB_PATH", os.path.join(os.path.dirname(__file__), "activos_data.db"))
    if not os.path.exists(db_path):
        return []
        
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        query = '''
            SELECT a.symbol, n.headline, n.sentiment, n.impact_score, n.published_at 
            FROM news_analysis n
            JOIN assets a ON n.asset_id = a.id
            WHERE a.type IN ('stock', 'index')
            ORDER BY n.published_at DESC, n.id DESC
        '''
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

async def get_asset_stats():
    db_path = os.getenv("ACTIVOS_DB_PATH", os.path.join(os.path.dirname(__file__), "activos_data.db"))
    if not os.path.exists(db_path):
        return []
        
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        query = '''
            SELECT a.symbol, a.name, 
                   (SELECT COUNT(*) FROM price_history p WHERE p.asset_id = a.id) as candle_count,
                   (SELECT COUNT(*) FROM news_analysis n WHERE n.asset_id = a.id) as news_count
            FROM assets a
            WHERE a.type IN ('stock', 'index')
            ORDER BY a.symbol
        '''
        async with db.execute(query) as cursor:
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]
