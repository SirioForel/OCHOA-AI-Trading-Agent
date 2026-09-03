from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import sqlite3
import os
from typing import List, Optional
import sys

# Añadir directorio raíz a path para poder importar db.py
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import db

app = FastAPI(title="Activos Digueti API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AssetCreate(BaseModel):
    symbol: str
    name: str
    asset_type: str # 'stock' o 'crypto'

# Asegurar que la base de datos se inicializa
db.init_db()

def run_scraper_task():
    """Ejecuta el scraper en segundo plano."""
    try:
        # Importamos de forma dinámica para evitar problemas de path
        sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'scraper'))
        import scraper
        scraper.run_scraper_cycle()
    except Exception as e:
        print(f"Error running scraper background task: {e}")

@app.get("/api/assets")
async def get_assets():
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT id, symbol, name, type, last_price, change_percent, datetime(updated_at, 'localtime') as updated_at 
        FROM assets 
        ORDER BY type DESC, symbol ASC
    ''')
    assets = [dict(r) for r in cursor.fetchall()]
    for a in assets:
        cursor.execute("SELECT price FROM price_history WHERE asset_id = ? ORDER BY timestamp DESC LIMIT 12", (a['id'],))
        prices = [r[0] for r in cursor.fetchall()]
        prices.reverse()
        a['sparkline'] = prices
    conn.close()
    return assets

@app.get("/api/assets/detail/{asset_id}")
async def get_asset_detail(asset_id: int):
    conn = db.get_connection()
    cursor = conn.cursor()
    
    # 1. Metadatos del activo
    cursor.execute("SELECT * FROM assets WHERE id = ?", (asset_id,))
    asset_row = cursor.fetchone()
    if not asset_row:
        conn.close()
        raise HTTPException(status_code=404, detail="Asset not found")
        
    asset_info = dict(asset_row)
    
    # 2. Historial de precios (últimos 150 puntos para renderizar un buen gráfico)
    cursor.execute('''
        SELECT price, datetime(timestamp, 'localtime') as date
        FROM price_history
        WHERE asset_id = ?
        ORDER BY timestamp ASC
    ''')
    history_rows = cursor.fetchall()
    
    # Si tenemos demasiados puntos, reducimos para optimizar la carga del frontend
    # (ej. tomamos uno cada N si supera los 150 puntos)
    history = [dict(h) for h in history_rows]
    if len(history) > 150:
        step = len(history) // 150
        history = history[::step]
        
    # 3. Noticias analizadas cruzadas
    cursor.execute('''
        SELECT id, headline, url, sentiment, impact_score, source, 
               datetime(published_at, 'localtime') as published_at,
               price_at_news, price_1h_later, price_4h_later, price_24h_later
        FROM news_analysis
        WHERE asset_id = ?
        ORDER BY published_at DESC
        LIMIT 50
    ''', (asset_id,))
    news_rows = cursor.fetchall()
    
    conn.close()
    
    return {
        "asset": asset_info,
        "history": history,
        "news": [dict(n) for n in news_rows]
    }

@app.get("/api/news")
async def get_news(limit: int = 30):
    conn = db.get_connection()
    cursor = conn.cursor()
    cursor.execute(f'''
        SELECT n.id, n.headline, n.url, n.sentiment, n.impact_score, n.source,
               datetime(n.published_at, 'localtime') as published_at, 
               n.price_at_news, n.price_1h_later, n.price_4h_later, n.price_24h_later,
               a.symbol, a.name as asset_name, a.id as asset_id
        FROM news_analysis n
        JOIN assets a ON n.asset_id = a.id
        ORDER BY n.published_at DESC
        LIMIT ?
    ''', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/alerts")
async def get_alerts():
    conn = db.get_connection()
    cursor = conn.cursor()
    # Filtramos alertas de impacto significativo: insider_sell, regulaciones fuertes, o muy negativos
    cursor.execute('''
        SELECT n.id, n.headline, n.url, n.sentiment, n.impact_score, n.source,
               datetime(n.published_at, 'localtime') as published_at,
               a.symbol, a.name as asset_name
        FROM news_analysis n
        JOIN assets a ON n.asset_id = a.id
        WHERE (n.sentiment IN ('insider_sell', 'regulatory') OR n.impact_score <= -30 OR n.impact_score >= 40)
        ORDER BY n.published_at DESC
        LIMIT 10
    ''')
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.post("/api/assets")
async def create_asset(asset: AssetCreate, background_tasks: BackgroundTasks):
    conn = db.get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO assets (symbol, name, type) VALUES (?, ?, ?)",
            (asset.symbol.upper(), asset.name, asset.asset_type.lower())
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        raise HTTPException(status_code=400, detail="Asset ticker already exists")
    finally:
        conn.close()
        
    # Lanzar el scraper en segundo plano para poblar precios y noticias de inmediato
    background_tasks.add_task(run_scraper_task)
    return {"status": "success", "message": f"Asset {asset.symbol} added successfully. Scraper triggered."}

@app.post("/api/admin/scrape")
async def trigger_scrape(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_scraper_task)
    return {"status": "success", "message": "Scraper cycle triggered in background."}

# Servir estáticos en la raíz (después de definir las APIs)
frontend_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'frontend'))
if os.path.exists(frontend_path):
    app.mount("/", StaticFiles(directory=frontend_path, html=True), name="frontend")
