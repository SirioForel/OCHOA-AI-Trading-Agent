import sqlite3
import pandas as pd
import math
import os
import time

DB_PATH = os.getenv("ACTIVOS_DB_PATH", '/home/soporte/ochoa/activos_data.db')

import ccxt

class BrokerClient:
    def __init__(self, api_key: str = None, api_secret: str = None, is_live: bool = False):
        import dotenv
        if not api_key or not api_secret:
            dotenv.load_dotenv()
            api_key = api_key or os.getenv("ALPACA_API_KEY")
            api_secret = api_secret or os.getenv("ALPACA_SECRET_KEY")
        self.api_key = api_key
        self.api_secret = api_secret
        self.db_path = os.getenv("ACTIVOS_DB_PATH", '/home/soporte/ochoa/activos_data.db')
        self._balances = {'USD': 10000.0, 'MSFT': 0.0, 'GOOGL': 0.0, 'NVDA': 0.0, 'AAPL': 0.0, 'AMZN': 0.0, 'META': 0.0, 'TSLA': 0.0}
        self.is_live = is_live
        
        if api_key and api_secret:
            self.exchange = ccxt.alpaca({
                'apiKey': api_key,
                'secret': api_secret,
            })
            self.exchange.set_sandbox_mode(not self.is_live)
        else:
            self.exchange = None

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def get_price(self, symbol: str) -> float:
        try:
            with self._get_conn() as conn:
                c = conn.cursor()
                res = c.execute("SELECT last_price FROM assets WHERE symbol=?", (symbol,)).fetchone()
                if res:
                    return float(res[0])
        except Exception as e:
            print(f"Error getting price for {symbol}: {e}")
        return 0.0

    def get_trend_24h(self, symbol: str) -> float:
        try:
            with self._get_conn() as conn:
                c = conn.cursor()
                res = c.execute("SELECT change_percent FROM assets WHERE symbol=?", (symbol,)).fetchone()
                if res:
                    return float(res[0])
        except Exception as e:
            print(f"Error getting trend for {symbol}: {e}")
        return 0.0

    def is_market_open(self) -> bool:
        if self.api_key and self.api_secret:
            import requests
            base_url = "https://api.alpaca.markets" if self.is_live else "https://paper-api.alpaca.markets"
            headers = {"APCA-API-KEY-ID": self.api_key, "APCA-API-SECRET-KEY": self.api_secret}
            try:
                resp = requests.get(f"{base_url}/v2/clock", headers=headers, timeout=5)
                if resp.status_code == 200:
                    return resp.json().get("is_open", True)
            except Exception as e:
                print(f"Alpaca clock connection failed: {e}")
        return True

    def get_balance(self, currency: str, portfolio_id: str = None) -> float:
        if self.api_key and self.api_secret:
            import requests
            base_url = "https://api.alpaca.markets" if self.is_live else "https://paper-api.alpaca.markets"
            headers = {"APCA-API-KEY-ID": self.api_key, "APCA-API-SECRET-KEY": self.api_secret}
            try:
                if currency == 'USD':
                    resp = requests.get(f"{base_url}/v2/account", headers=headers, timeout=10)
                    if resp.status_code == 200:
                        return float(resp.json().get("buying_power", 0.0))
                else:
                    resp = requests.get(f"{base_url}/v2/positions/{currency}", headers=headers, timeout=10)
                    if resp.status_code == 200:
                        data = resp.json()
                        return float(data.get("qty_available", data.get("qty", 0.0)))
                    elif resp.status_code == 404:
                        return 0.0
            except Exception as e:
                print(f"Alpaca connection failed, falling back to simulation: {e}")
        return self._balances.get(currency, 0.0)

    def get_portfolio_history(self, period='1D', timeframe='5Min'):
        if self.api_key and self.api_secret:
            import requests
            base_url = "https://api.alpaca.markets" if self.is_live else "https://paper-api.alpaca.markets"
            headers = {"APCA-API-KEY-ID": self.api_key, "APCA-API-SECRET-KEY": self.api_secret}
            try:
                params = {"period": period, "timeframe": timeframe}
                resp = requests.get(f"{base_url}/v2/account/portfolio/history", headers=headers, params=params, timeout=10)
                if resp.status_code == 200:
                    data = resp.json()
                    import datetime
                    labels = []
                    for t in data.get("timestamp", []):
                        dt = datetime.datetime.fromtimestamp(t)
                        labels.append(f"{dt.hour:02d}:{dt.minute:02d}")
                    return {
                        "labels": labels,
                        "data": data.get("equity", []),
                        "base_value": data.get("base_value", 0)
                    }
            except Exception as e:
                print(f"Error fetching portfolio history: {e}")
        return None

    def buy_market_usd(self, symbol: str, usd_amount: float, portfolio_id: str = None) -> dict:
        print(f"[BrokerClient] Executing BUY for {symbol} with {usd_amount} USD")
        if self.api_key and self.api_secret:
            import requests
            base_url = "https://api.alpaca.markets" if self.is_live else "https://paper-api.alpaca.markets"
            headers = {"APCA-API-KEY-ID": self.api_key, "APCA-API-SECRET-KEY": self.api_secret, "Content-Type": "application/json"}
            payload = {
                "symbol": symbol,
                "notional": str(round(usd_amount, 2)), # Alpaca supports fractional trading via notional amount
                "side": "buy",
                "type": "market",
                "time_in_force": "day"
            }
            try:
                resp = requests.post(f"{base_url}/v2/orders", headers=headers, json=payload, timeout=10)
                if resp.status_code in [200, 201]:
                    data = resp.json()
                    order_id = data.get('id')
                    import time
                    for _ in range(3):
                        time.sleep(1)
                        c_resp = requests.get(f"{base_url}/v2/orders/{order_id}", headers=headers)
                        if c_resp.status_code == 200:
                            c_data = c_resp.json()
                            if c_data.get('status') == 'filled':
                                f_qty = float(c_data.get('filled_qty', 0.0))
                                f_price = float(c_data.get('filled_avg_price', 0.0))
                                return {'status': 'closed', 'filled': f_qty, 'cost': f_qty * f_price, 'price': f_price, 'order_id': order_id}
                    
                    price_est = self.get_price(symbol)
                    return {
                        'status': 'closed',
                        'filled': base_amount,
                        'cost': base_amount * price_est,
                        'price': price_est,
                        'order_id': order_id
                    }
                else:
                    print(f"Alpaca API Error (buy): {resp.text}")
                    # Force raise to trigger fallback simulation
                    raise Exception(f"Alpaca API Error: {resp.text}")
            except Exception as e:
                print(f"Alpaca BUY execution failed, falling back to simulation: {e}")

        price = self.get_price(symbol)
        if price <= 0: return None
        amount = usd_amount / price
        self._balances['USD'] -= usd_amount
        self._balances[symbol] = self._balances.get(symbol, 0.0) + amount
        return {
            'status': 'closed',
            'filled': amount,
            'cost': usd_amount,
            'price': price
        }

    def sell_market_base(self, symbol: str, base_amount: float, portfolio_id: str = None) -> dict:
        print(f"[BrokerClient] Executing SELL for {symbol} with {base_amount} shares")
        if self.api_key and self.api_secret:
            import requests
            base_url = "https://api.alpaca.markets" if self.is_live else "https://paper-api.alpaca.markets"
            headers = {"APCA-API-KEY-ID": self.api_key, "APCA-API-SECRET-KEY": self.api_secret, "Content-Type": "application/json"}
            import math
            # Alpaca supports fractional shares up to 9 decimals. 
            # We truncate to 8 decimals (using floor) to NEVER exceed the available balance due to rounding UP.
            truncated_qty = math.floor(base_amount * 100000000) / 100000000
            # If the value has no decimals after floor, it will be .0 which is fine. To be clean we format it:
            qty_str = "{:.8f}".format(truncated_qty).rstrip('0').rstrip('.')
            if not qty_str: qty_str = "0"
            payload = {
                "symbol": symbol,
                "qty": qty_str,
                "side": "sell",
                "type": "market",
                "time_in_force": "day"
            }
            try:
                resp = requests.post(f"{base_url}/v2/orders", headers=headers, json=payload, timeout=10)
                if resp.status_code in [200, 201]:
                    data = resp.json()
                    order_id = data.get('id')
                    import time
                    for _ in range(3):
                        time.sleep(1)
                        c_resp = requests.get(f"{base_url}/v2/orders/{order_id}", headers=headers)
                        if c_resp.status_code == 200:
                            c_data = c_resp.json()
                            if c_data.get('status') == 'filled':
                                f_qty = float(c_data.get('filled_qty', 0.0))
                                f_price = float(c_data.get('filled_avg_price', 0.0))
                                return {'status': 'closed', 'filled': f_qty, 'cost': f_qty * f_price, 'price': f_price, 'order_id': order_id}
                    
                    price_est = self.get_price(symbol)
                    return {
                        'status': 'closed',
                        'filled': base_amount,
                        'cost': base_amount * price_est,
                        'price': price_est,
                        'order_id': order_id
                    }
                else:
                    print(f"Alpaca API Error (sell): {resp.text}")
                    raise Exception(f"Alpaca API Error: {resp.text}")
            except Exception as e:
                print(f"Alpaca SELL execution failed, falling back to simulation: {e}")

        price = self.get_price(symbol)
        if price <= 0: return None
        revenue = base_amount * price
        self._balances[symbol] = max(0.0, self._balances.get(symbol, 0.0) - base_amount)
        self._balances['USD'] += revenue
        return {
            'status': 'closed',
            'filled': base_amount,
            'cost': revenue,
            'price': price
        }

    def fetch_ohlcv(self, symbol, timeframe='15m', limit=100):
        try:
            with self._get_conn() as conn:
                c = conn.cursor()
                res = c.execute('''
                    SELECT price, timestamp 
                    FROM price_history 
                    JOIN assets ON assets.id = price_history.asset_id 
                    WHERE assets.symbol = ? 
                    ORDER BY timestamp DESC LIMIT ?
                ''', (symbol, limit)).fetchall()
                if not res: return []
                res.reverse()
                import datetime
                ohlcv = []
                for row in res:
                    price = float(row[0])
                    ts_str = row[1]
                    try:
                        dt = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                    except:
                        dt = datetime.datetime.now()
                    ts_ms = int(dt.timestamp() * 1000)
                    ohlcv.append([ts_ms, price, price, price, price, 1000])
                return ohlcv
        except Exception as e:
            print(f"Error fetching ohlcv for {symbol}: {e}")
            return []

    def fetch_alpaca_daily_bars(self, symbol, limit=210, timeframe="1Day"):
        """Fetch historical bars using Alpaca Market Data API v2"""
        url = f"https://data.alpaca.markets/v2/stocks/{symbol}/bars"
        from datetime import datetime, timedelta
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=365) # Need enough calendar days to get 210 trading days
        params = {
            "timeframe": timeframe,
            "limit": limit,
            "start": start_dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
            "end": end_dt.strftime('%Y-%m-%dT%H:%M:%SZ'),
            "feed": "iex"
        }
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret
        }
        try:
            import requests
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                bars = data.get('bars', [])
                if not bars: return []
                import datetime
                ohlcv = []
                for b in bars:
                    # b: {'t': '2023-01-01T...', 'o': 100, 'h': 105, 'l': 95, 'c': 102, 'v': 1000}
                    ts_str = b['t'].replace('Z', '+00:00')
                    dt = datetime.datetime.fromisoformat(ts_str)
                    ts_ms = int(dt.timestamp() * 1000)
                    ohlcv.append([ts_ms, b['o'], b['h'], b['l'], b['c'], b['v']])
                return ohlcv
        except Exception as e:
            print(f"Error fetch_alpaca_daily_bars para {symbol}: {e}")
        return []
