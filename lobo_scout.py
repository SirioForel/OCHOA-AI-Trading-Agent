import sys
import os
import json
import sqlite3
from datetime import datetime
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import MACD

sys.path.append('/home/soporte/ochoa')
from broker_client import BrokerClient
from predictive_brain import PredictiveBrain

sys.path.append('/home/soporte/ochoa/scraper')
try:
    from scraper import fetch_google_news, analyze_headline
except ImportError:
    fetch_google_news = None

def inject_jit_news(symbol, headline, sentiment, impact, source, url):
    with sqlite3.connect('/home/soporte/ochoa/activos_data.db') as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM assets WHERE symbol = ?", (symbol,))
        res = cursor.fetchone()
        if not res:
            cursor.execute("INSERT INTO assets (symbol, name, type) VALUES (?, ?, ?)", (symbol, symbol, 'stock'))
            asset_id = cursor.lastrowid
        else:
            asset_id = res[0]
        cursor.execute('''
            INSERT OR IGNORE INTO news_analysis 
            (asset_id, headline, url, sentiment, impact_score, source, published_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (asset_id, headline, url, sentiment, impact, source, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()

def process_bot(bot):
    bot_id, day_trader, overdrive, radar_size, asset_universe, buy_threshold, sell_threshold, api_key, api_secret = bot
    print(f"\n--- Running Scout for Bot ID {bot_id} (Universe: {asset_universe}) ---")
    
    filename = '/home/soporte/ochoa/mag7_tickers.txt' if asset_universe == 'MAG7' else '/home/soporte/ochoa/sp500_tickers.txt'
    with open(filename, 'r') as f:
        tickers = [line.strip() for line in f if line.strip()]

    client = BrokerClient(api_key=api_key, api_secret=api_secret)
    brain = PredictiveBrain()
    
    # Filter owned symbols
    import requests
    try:
        resp = requests.get(
            "https://paper-api.alpaca.markets/v2/positions", 
            headers={"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret},
            timeout=5
        )
        if resp.status_code == 200:
            owned_symbols = []
            for p in resp.json():
                sym = p['symbol']
                if p['asset_class'] == 'us_option' and len(sym) > 15:
                    owned_symbols.append(sym[:-15])
                else:
                    owned_symbols.append(sym)
            tickers = [t for t in tickers if t not in owned_symbols]
    except Exception as e:
        print(f"Failed to filter positions for bot {bot_id}: {e}")

    results = []
    tf = '15Min' if day_trader else '1Day'
    
    for symbol in tickers:
        try:
            bars = client.fetch_alpaca_daily_bars(symbol, limit=210, timeframe=tf)
            if not bars or len(bars) < 50:
                bars = client.fetch_ohlcv(symbol, timeframe='1d', limit=210)
                if not bars or len(bars) < 50:
                    continue
                
            df = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            rsi_indicator = RSIIndicator(close=df['close'], window=14)
            rsi = rsi_indicator.rsi().iloc[-1]
            macd_ind = MACD(close=df['close'], window_fast=12, window_slow=26)
            macd = macd_ind.macd().iloc[-1]
            macd_signal = macd_ind.macd_signal().iloc[-1]
            macd_hist = macd - macd_signal
            
            if pd.isna(rsi):
                continue
                
            results.append({
                "symbol": symbol,
                "rsi": rsi,
                "macd_hist": macd_hist,
                "df": df
            })
        except Exception as e:
            pass # suppress per-symbol errors to keep logs clean
            
    if asset_universe == 'MAG7':
        top_candidates = results
    else:
        results.sort(key=lambda x: x['rsi'])
        half_size = max(1, radar_size // 2)
        top_oversold = results[:half_size]
        top_overbought = results[-half_size:] if len(results) >= half_size else []
        top_candidates = []
        for item in top_oversold + top_overbought:
            if item not in top_candidates:
                top_candidates.append(item)
            
    final_output = []
    for item in top_candidates:
        symbol = item['symbol']
        df = item['df']
        rsi = item['rsi']
        price = item['df']['close'].iloc[-1]
        
        if overdrive and (rsi < 10 or rsi > 90):
            print(f"[{bot_id}] OVERDRIVE TRIGGERED FOR {symbol} (RSI: {rsi:.1f})")
            action = "BUY" if rsi < 10 else "PUT"
            score = 100 if rsi < 10 else 0
            final_output.append({
                "symbol": symbol,
                "score": score,
                "action": action,
                "reason": f"OVERDRIVE MODE | RSI Extremo ({rsi:.1f})",
                "price": price
            })
            continue

        if fetch_google_news:
            try:
                news_items = fetch_google_news(symbol, symbol)
                if news_items:
                    top_news = news_items[0]
                    headline = top_news["title"]
                    sentiment, impact = analyze_headline(headline, symbol, symbol)
                    inject_jit_news(symbol, headline, sentiment, impact, top_news['source'], top_news['url'])
            except Exception as e:
                print(f"[{bot_id}] JIT LLM Error for {symbol}: {e}")
        
        analysis = brain.analyze_market_data(df, symbol=symbol, buy_threshold=buy_threshold, sell_threshold=sell_threshold)
        price = float(df['close'].iloc[-1])
        
        final_output.append({
            "symbol": symbol,
            "score": analysis.get('score', 0),
            "action": analysis.get('action', 'HOLD'),
            "reason": analysis.get('reason', ""),
            "price": price
        })
        
    with open(f"/home/soporte/ochoa/scout_results_{bot_id}.json", "w") as f:
        json.dump(final_output, f, indent=4)

def main():
    active_bots = []
    try:
        with sqlite3.connect('/home/soporte/ochoa/ochoa.db') as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, day_trader, overdrive, radar_size, asset_universe, buy_threshold, sell_threshold, api_key, api_secret 
                FROM bots WHERE is_active = 1
            """)
            for row in cursor.fetchall():
                active_bots.append((
                    row[0], 
                    bool(row[1]), 
                    bool(row[2]), 
                    int(row[3]) if row[3] else 20, 
                    row[4] if row[4] else 'SP500', 
                    int(row[5]) if row[5] else 75, 
                    int(row[6]) if row[6] else 25,
                    row[7],
                    row[8]
                ))
    except Exception as e:
        print(f"Database error loading bots: {e}")
        return

    for bot in active_bots:
        try:
            process_bot(bot)
        except Exception as e:
            print(f"Error processing bot {bot[0]}: {e}")

if __name__ == "__main__":
    main()
