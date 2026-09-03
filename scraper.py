import sqlite3
import os
import requests
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import json
import time
import email.utils
from datetime import datetime, timedelta

# --- CONFIGURACIÓN ---
DB_PATH = '/home/soporte/ochoa/activos_data.db'
OLLAMA_URL = 'http://127.0.0.1:11434/api/generate'
OLLAMA_MODEL = 'llama3.2'
LLM_PROVIDER = 'gemini' # 'ollama' o 'gemini'
GEMINI_API_KEY = "TU_API_KEY_AQUI"


HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)'}

def get_db_connection():
    return sqlite3.connect(DB_PATH)

def fetch_yahoo_price(ticker):
    """Obtiene el precio actual y el cambio porcentual de Yahoo Finance."""
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=5m'
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return None, None
        meta = r.json()['chart']['result'][0]['meta']
        price = meta.get('regularMarketPrice')
        prev_close = meta.get('previousClose')
        
        change_pct = 0.0
        if price and prev_close:
            change_pct = round(((price - prev_close) / prev_close) * 100, 2)
            
        return price, change_pct
    except Exception as e:
        print(f"Error fetching Yahoo price for {ticker}: {e}")
        return None, None

def fetch_price_at_timestamp(ticker, target_timestamp):
    """
    Obtiene el precio histórico más cercano a un timestamp objetivo usando Yahoo Finance.
    Esto permite calcular de forma retroactiva el precio 1h, 4h y 24h después.
    """
    # Solicitamos un rango de 5 días alrededor del timestamp para asegurar cobertura
    start_time = int(target_timestamp - 3600 * 12)
    end_time = int(target_timestamp + 3600 * 12)
    
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?period1={start_time}&period2={end_time}&interval=15m'
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        
        result = r.json()['chart']['result'][0]
        timestamps = result.get('timestamp', [])
        quotes = result.get('indicators', {}).get('quote', [{}])[0].get('close', [])
        
        if not timestamps or not quotes:
            return None
            
        # Buscar el índice con el timestamp más cercano
        closest_idx = min(range(len(timestamps)), key=lambda i: abs(timestamps[i] - target_timestamp) if quotes[i] is not None else float('inf'))
        
        if abs(timestamps[closest_idx] - target_timestamp) < 3600 * 4: # Tolerancia de 4 horas
            return quotes[closest_idx]
            
        return None
    except Exception as e:
        print(f"Error fetching price at timestamp for {ticker}: {e}")
        return None

def fetch_and_populate_history(asset_id, symbol):
    """Rellena el historial de precios de los últimos 30 días si está vacío."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM price_history WHERE asset_id = ?", (asset_id,))
    if cursor.fetchone()[0] > 0:
        conn.close()
        return
        
    print(f"📈 Población de historial de 30 días para {symbol}...")
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=30d&interval=1d'
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code == 200:
            result = r.json()['chart']['result'][0]
            timestamps = result.get('timestamp', [])
            closes = result.get('indicators', {}).get('quote', [{}])[0].get('close', [])
            
            history_data = []
            for t, c in zip(timestamps, closes):
                if c is not None:
                    dt_str = datetime.fromtimestamp(t).strftime('%Y-%m-%d %H:%M:%S')
                    history_data.append((asset_id, round(c, 4), dt_str))
            
            cursor.executemany('''
                INSERT INTO price_history (asset_id, price, timestamp) VALUES (?, ?, ?)
            ''', history_data)
            conn.commit()
            print(f"   ✅ Insertados {len(history_data)} puntos históricos para {symbol}.")
    except Exception as e:
        print(f"Error al poblar historial de {symbol}: {e}")
    finally:
        conn.close()

def analyze_headline(headline, asset_name, symbol):
    """Llama a Ollama (Llama 3.2) o Gemini para extraer sentimiento e impacto."""
    prompt = f"""
    You are a financial news analysis AI. Read this news headline about the asset "{asset_name}" (Ticker: {symbol}):
    "{headline}"

    Extract:
    1. sentiment: choose exactly one value from: ['positivo', 'negativo', 'neutral', 'insider_sell', 'insider_buy', 'regulatory'].
       - 'insider_sell' is for founders/CEOs/directors/large-shareholders selling shares/tokens.
       - 'insider_buy' is for insiders buying.
       - 'regulatory' is for SEC, laws, court rulings, approvals/bans.
       - 'positivo' is for good earnings, partnerships, upgrades.
       - 'negativo' is for bad earnings, hacks, layoffs, downgrades.
       - 'neutral' is for standard updates.
    2. impact_score: an integer from -100 (extremely bearish) to +100 (extremely bullish). 
       E.g., CEO selling massive shares/investigation = -50 to -90. SEC approval = +70 to +90.

    Return ONLY a valid JSON. No markdown, no backticks. Example:
    {{"sentiment": "insider_sell", "impact_score": -60}}
    """
    
    if LLM_PROVIDER == 'gemini':
        # Integración con Gemini
        try:
            from google import genai
            client = genai.Client(api_key=GEMINI_API_KEY)
            response = client.models.generate_content(
                model='gemini-2.5-flash-lite',
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            raw_text = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(raw_text)
            return data.get("sentiment", "neutral").lower(), int(data.get("impact_score", 0))
        except Exception as e:
            print(f"Gemini API error, falling back to local Ollama: {e}")
            
    # Integración con Ollama (Llama 3.2)
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.0}
    }
    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=45)
        raw_resp = response.json().get("response", "{}")
        parsed = json.loads(raw_resp)
        sentiment = parsed.get("sentiment", "neutral").lower()
        if sentiment not in ['positivo', 'negativo', 'neutral', 'insider_sell', 'insider_buy', 'regulatory']:
            sentiment = 'neutral'
        return sentiment, int(parsed.get("impact_score", 0))
    except Exception as e:
        print(f"Ollama local error: {e}")
        return "neutral", 0

def fetch_google_news(asset_name, symbol):
    """Busca noticias de Google News RSS para un activo en ES y EN."""
    clean_symbol = symbol.replace(".MC", "")
    if symbol == "DX-Y.NYB":
        clean_symbol = "DXY"
    if symbol == "^VIX":
        clean_symbol = "VIX"
        
    # Query simple para Google News
    # Forzamos contexto financiero para evitar falsos positivos con siglas (ej: "BA" -> "Papa Dame Ba")
    q_str = f'"{asset_name} stock" OR "{asset_name} acciones" OR "NYSE:{clean_symbol}" OR "NASDAQ:{clean_symbol}"'
    if symbol == "^VIX":
        q_str = '"índice VIX" OR "CBOE VIX" OR "volatilidad VIX"'
        
    headlines_dict = {}
    
    # Rss sources (ES y US)
    feeds = [
        {"hl": "es", "gl": "ES", "ceid": "ES:es"},
        {"hl": "en", "gl": "US", "ceid": "US:en"}
    ]
    
    for f in feeds:
        query_quoted = urllib.parse.quote(q_str)
        url = f"https://news.google.com/rss/search?q={query_quoted}&hl={f['hl']}&gl={f['gl']}&ceid={f['ceid']}"
        
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=10) as response:
                xml_data = response.read()
                root = ET.fromstring(xml_data)
                
                for item in root.findall('./channel/item')[:8]:
                    title = item.find('title').text
                    
                    # Filtros estrictos en Python (por si Google News ignora comillas)
                    title_lower = title.lower()
                    if symbol == "SAN" and "santander" not in title_lower:
                        continue
                    if symbol == "FER" and "ferrovial" not in title_lower:
                        continue
                    if "unicaja" in title_lower and any(word in title_lower for word in ["baloncesto", "basket", "fichaje", "coach", "fiba", "cantera", "staff", "partido"]):
                        continue
                    if symbol == "^VIX" and any(word in title_lower for word in ["streaming", "premium", "fútbol", "futbol", "vivo", "partido", "tv", "canal", "ver "]):
                        continue
                    link = item.find('link').text
                    pub_date = item.find('pubDate')
                    date_str = pub_date.text if pub_date is not None else None
                    source_elem = item.find('source')
                    source = source_elem.text if source_elem is not None else "Google News"
                    
                    headlines_dict[link] = {
                        "title": title,
                        "url": link,
                        "pub_date": date_str,
                        "source": source
                    }
        except Exception as e:
            print(f"Error parsing Google News RSS ({f['hl']}): {e}")
            
    return list(headlines_dict.values())

def update_post_news_prices(conn):
    """Calcula y rellena retroactivamente los precios a 1h, 4h y 24h de las noticias."""
    cursor = conn.cursor()
    
    # Obtener noticias que necesiten actualización
    cursor.execute('''
        SELECT n.id, n.published_at, n.price_at_news, n.price_1h_later, n.price_4h_later, n.price_24h_later, a.symbol
        FROM news_analysis n
        JOIN assets a ON n.asset_id = a.id
        WHERE n.price_1h_later IS NULL OR n.price_4h_later IS NULL OR n.price_24h_later IS NULL
    ''')
    rows = cursor.fetchall()
    
    now = datetime.now()
    
    for r in rows:
        n_id, pub_at_str, p_at_news, p_1h, p_4h, p_24h, symbol = r
        
        try:
            pub_dt = datetime.strptime(pub_at_str, "%Y-%m-%d %H:%M:%S")
        except:
            continue
            
        pub_ts = int(pub_dt.timestamp())
        
        # 1 hora más tarde
        if p_1h is None and (now - pub_dt) > timedelta(hours=1):
            price = fetch_price_at_timestamp(symbol, pub_ts + 3600)
            if price:
                cursor.execute("UPDATE news_analysis SET price_1h_later = ? WHERE id = ?", (round(price, 4), n_id))
                print(f"   ⏱️ Actualizado precio +1h para {symbol}: {price}")
                
        # 4 horas más tarde
        if p_4h is None and (now - pub_dt) > timedelta(hours=4):
            price = fetch_price_at_timestamp(symbol, pub_ts + 3600 * 4)
            if price:
                cursor.execute("UPDATE news_analysis SET price_4h_later = ? WHERE id = ?", (round(price, 4), n_id))
                print(f"   ⏱️ Actualizado precio +4h para {symbol}: {price}")
                
        # 24 horas más tarde
        if p_24h is None and (now - pub_dt) > timedelta(days=1):
            price = fetch_price_at_timestamp(symbol, pub_ts + 3600 * 24)
            if price:
                cursor.execute("UPDATE news_analysis SET price_24h_later = ? WHERE id = ?", (round(price, 4), n_id))
                print(f"   ⏱️ Actualizado precio +24h para {symbol}: {price}")
                
    conn.commit()

def run_scraper_cycle():
    print(f"🚀 Iniciando ciclo de scraping a las: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 1. Obtener todos los activos
    cursor.execute("SELECT id, symbol, name, type FROM assets")
    assets = cursor.fetchall()
    
    for asset_id, symbol, name, type_ in assets:
        print(f"\n📊 Procesando {name} ({symbol})...")
        
        # A. Actualizar precio actual
        price, change_pct = fetch_yahoo_price(symbol)
        if price is not None:
            cursor.execute('''
                UPDATE assets 
                SET last_price = ?, change_percent = ?, updated_at = CURRENT_TIMESTAMP 
                WHERE id = ?
            ''', (price, change_pct, asset_id))
            
            # Guardar en el historial
            cursor.execute('''
                INSERT INTO price_history (asset_id, price) VALUES (?, ?)
            ''', (asset_id, price))
            conn.commit()
            print(f"   💵 Precio actual: {price} | Cambio 24h: {change_pct}%")
        else:
            print("   ⚠️ No se pudo obtener el precio actual.")
            # Intentamos leer el último precio de la base de datos para no fallar
            cursor.execute("SELECT last_price FROM assets WHERE id = ?", (asset_id,))
            price_row = cursor.fetchone()
            price = price_row[0] if price_row else None
            
        # B. Poblar 30 días si el historial está vacío
        fetch_and_populate_history(asset_id, symbol)
        
        # C. Rascar noticias y analizar
        news_items = fetch_google_news(name, symbol)
        print(f"   📰 Encontradas {len(news_items)} noticias.")
        
        for item in news_items:
            title = item["title"]
            url = item["url"]
            source = item["source"]
            pub_date_str = item["pub_date"]
            
            # Evitar duplicados
            cursor.execute("SELECT id FROM news_analysis WHERE headline = ?", (title,))
            if cursor.fetchone():
                continue
                
            print(f"      🧠 Analizando: {title[:70]}...")
            sentiment, impact = analyze_headline(title, name, symbol)
            print(f"         -> {sentiment.upper()} (Impacto: {impact})")
            
            # Parsear fecha
            db_date = None
            if pub_date_str:
                try:
                    dt = email.utils.parsedate_to_datetime(pub_date_str)
                    db_date = dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    db_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            else:
                db_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
            # Guardar noticia con el precio del activo en el momento de la noticia
            cursor.execute('''
                INSERT INTO news_analysis 
                (asset_id, headline, url, sentiment, impact_score, source, published_at, price_at_news)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (asset_id, title, url, sentiment, impact, source, db_date, price))
            conn.commit()
            
    # 2. Correlacionar precios post-noticia
    print("\n⏱️ Actualizando precios correlativos post-noticia...")
    update_post_news_prices(conn)
    
    conn.close()
    print("\n🏁 Ciclo completado con éxito.")

if __name__ == '__main__':
    run_scraper_cycle()
