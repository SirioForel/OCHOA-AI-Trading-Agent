import pandas as pd
import json
import os
import joblib
from ta.momentum import RSIIndicator, StochasticOscillator
from ta.trend import MACD, EMAIndicator
from ta.volatility import BollingerBands, AverageTrueRange

class PredictiveBrain:
    def __init__(self):
        self.min_data_points = 50
        self.db_path = os.path.join(os.path.dirname(__file__), 'activos_data.db')
        self._model_cache = None
        self._model_mtime = 0

    def _get_model(self, model_path):
        import os, joblib
        if not os.path.exists(model_path): return None
        mtime = os.path.getmtime(model_path)
        if self._model_cache is None or mtime > self._model_mtime:
            self._model_cache = joblib.load(model_path)
            self._model_mtime = mtime
        return self._model_cache

    def get_latest_sentiment(self, symbol, timestamp):
        clean_symbol = symbol.replace("/USDC", "").replace("/USD", "").replace("-USD", "")
        import sqlite3
        import pandas as pd
        from datetime import datetime, timedelta
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                res = cursor.execute("SELECT id FROM assets WHERE symbol = ? OR symbol = ?", (symbol, clean_symbol)).fetchone()
                if not res:
                    return 0.0
                asset_id = res[0]
                
                if isinstance(timestamp, pd.Timestamp):
                    ts_str = timestamp.strftime("%Y-%m-%d %H:%M:%S")
                else:
                    ts_str = str(timestamp)
                    
                try:
                    ts_obj = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                except:
                    ts_obj = datetime.now()
                    
                # Limitar la búsqueda a los últimos 5 días para saltar el fin de semana sin irnos demasiado atrás
                cutoff_date = ts_obj - timedelta(days=5)
                cutoff_str = cutoff_date.strftime("%Y-%m-%d %H:%M:%S")
                    
                # Extraer hasta las últimas 12 noticias de los últimos 5 días
                cursor.execute("""
                    SELECT sentiment, impact_score, published_at 
                    FROM news_analysis 
                    WHERE asset_id = ? AND published_at <= ? AND published_at >= ?
                    ORDER BY published_at DESC LIMIT 12
                """, (asset_id, ts_str, cutoff_str))
                rows = cursor.fetchall()
                if not rows:
                    return 0.0
                
                total_weight = 0.0
                weighted_impact = 0.0
                
                for sent_label, score, pub_at in rows:
                    try:
                        score = float(score)
                        if score > 100: score = 100.0
                        elif score < -100: score = -100.0
                    except:
                        score = 0.0
                    
                    sent_label = str(sent_label).lower() if sent_label is not None else "neutral"
                    if sent_label == 'positivo':
                        val = 1.0
                    elif sent_label == 'negativo':
                        val = -1.0
                    elif sent_label == 'neutral':
                        val = 0.0
                    elif sent_label == 'insider_buy':
                        val = 1.5
                    elif sent_label == 'insider_sell':
                        val = -1.5
                    elif sent_label == 'regulatory':
                        val = -1.0
                    else:
                        val = 0.0
                    
                    raw_impact = (score / 100.0) * abs(val) if val != 0 else 0.0
                    if raw_impact == 0.0 and val != 0.0:
                        raw_impact = val
                        
                    # Decadencia por tiempo (Time Decay)
                    try:
                        pub_date = datetime.strptime(pub_at, "%Y-%m-%d %H:%M:%S")
                        days_old = (ts_obj - pub_date).total_seconds() / 86400.0
                    except:
                        days_old = 0.0
                        
                    # La noticia pierde peso con los días: 1.0 hoy, bajando gradualmente hasta 0.2
                    weight = max(0.2, 1.0 - (abs(days_old) * 0.15))
                    
                    weighted_impact += raw_impact * weight
                    total_weight += weight
                
                if total_weight > 0:
                    return weighted_impact / total_weight
                return 0.0
        except Exception as e:
            print(f"Error getting aggregated sentiment for {symbol}: {e}")
        return 0.0

    def analyze_market_data(self, df_ohlcv: pd.DataFrame, symbol: str = None, buy_threshold: int = 75, sell_threshold: int = 25, ignore_ema: bool = False) -> dict:
        """
        Analiza un DataFrame OHLCV y retorna un score de 0 a 100 y una señal de acción.
        """
        if len(df_ohlcv) < self.min_data_points:
            return {"score": 50, "action": "HOLD", "reason": "Insufficient data"}

        # Cargar parámetros óptimos si existen
        rsi_w, macd_f, macd_s, ema_w = 14, 12, 26, 50
        if symbol and os.path.exists("optimal_params.json"):
            try:
                with open("optimal_params.json", "r") as f:
                    opt_params = json.load(f)
                    if symbol in opt_params:
                        p = opt_params[symbol]
                        rsi_w = p.get("rsi_window", 14)
                        macd_f = p.get("macd_fast", 12)
                        macd_s = p.get("macd_slow", 26)
                        ema_w = p.get("ema_window", 50)
            except Exception:
                pass

        # Calcular RSI
        rsi_indicator = RSIIndicator(close=df_ohlcv['close'], window=rsi_w)
        df_ohlcv['rsi'] = rsi_indicator.rsi()

        # Calcular MACD
        macd_indicator = MACD(close=df_ohlcv['close'], window_fast=macd_f, window_slow=macd_s)
        df_ohlcv['macd'] = macd_indicator.macd()
        df_ohlcv['macd_signal'] = macd_indicator.macd_signal()
        
        # Calcular EMA
        df_ohlcv['ema_200'] = EMAIndicator(close=df_ohlcv['close'], window=ema_w).ema_indicator()
        
        # --- MACHINE LEARNING INTEGRATION ---
        ml_prob_up = None
        if symbol:
            safe_sym = symbol.replace("/", "_")
            model_path = f"ml_model_{safe_sym}.pkl"
            if os.path.exists(model_path):
                try:
                    # Calcular features extra requeridos por el modelo
                    df_ohlcv['ema_50'] = EMAIndicator(close=df_ohlcv['close'], window=50).ema_indicator()
                    df_ohlcv['stoch'] = StochasticOscillator(high=df_ohlcv['high'], low=df_ohlcv['low'], close=df_ohlcv['close']).stoch()
                    
                    bb = BollingerBands(close=df_ohlcv['close'])
                    df_ohlcv['bb_low'] = bb.bollinger_lband()
                    df_ohlcv['atr'] = AverageTrueRange(high=df_ohlcv['high'], low=df_ohlcv['low'], close=df_ohlcv['close']).average_true_range()
                    
                    df_ohlcv['dist_ema_50'] = (df_ohlcv['close'] - df_ohlcv['ema_50']) / df_ohlcv['ema_50']
                    df_ohlcv['dist_bb_low'] = (df_ohlcv['close'] - df_ohlcv['bb_low']) / df_ohlcv['bb_low']
                    
                    last_row = df_ohlcv.iloc[-1]
                    sentiment_val = self.get_latest_sentiment(symbol, last_row['timestamp'])
                    
                    features = ['macd', 'macd_signal', 'rsi', 'stoch', 'atr', 'dist_ema_50', 'dist_bb_low', 'sentiment']
                    X_pred = pd.DataFrame([[
                        last_row['macd'], 
                        last_row['macd_signal'], 
                        last_row['rsi'], 
                        last_row['stoch'], 
                        last_row['atr'], 
                        last_row['dist_ema_50'], 
                        last_row['dist_bb_low'],
                        sentiment_val
                    ]], columns=['macd', 'macd_signal', 'rsi_14', 'stoch', 'atr', 'dist_ema_50', 'dist_bb_low', 'sentiment'])
                    
                    model = self._get_model(model_path)
                    probs = model.predict_proba(X_pred)
                    ml_prob_up = probs[0][1] * 100
                except Exception as e:
                    print(f"Error executing ML model for {symbol}: {e}")

        last_rsi = df_ohlcv['rsi'].iloc[-1]
        last_macd = df_ohlcv['macd'].iloc[-1]
        last_signal = df_ohlcv['macd_signal'].iloc[-1]
        last_price = df_ohlcv['close'].iloc[-1]
        last_ema_200 = df_ohlcv['ema_200'].iloc[-1] if 'ema_200' in df_ohlcv else None

        score = 50
        reason = []

        # Lógica de RSI
        if pd.notna(last_rsi):
            if last_rsi < 35:
                score += 25
                reason.append(f"RSI Sobrevendido ({last_rsi:.1f})")
            elif last_rsi > 65:
                score -= 25
                reason.append(f"RSI Sobrecomprado ({last_rsi:.1f})")

        # Lógica de MACD
        if pd.notna(last_macd) and pd.notna(last_signal):
            if last_macd > last_signal:
                score += 15
                reason.append("MACD Alcista")
            else:
                score -= 15
                reason.append("MACD Bajista")

        # Acotar score en rango de seguridad
        score = max(0, min(100, score))
        
        # Modificador Machine Learning
        if ml_prob_up is not None:
            reason.append(f"ML Predict (Prob Subida): {ml_prob_up:.1f}%")
            if ml_prob_up > 60:
                score += 15
            elif ml_prob_up < 40:
                score -= 15
                
        # Extraer motivo LLM si hay noticia reciente
        news_detail = self.get_latest_news_headline(symbol)
        if news_detail:
            reason.append(f"LLM: {news_detail}")
            # Extraer numéricamente el impacto LLM para afectar al score
            last_ts = df_ohlcv['timestamp'].iloc[-1] if 'timestamp' in df_ohlcv else pd.Timestamp.now()
            impact = self.get_latest_sentiment(symbol, last_ts)
            if impact != 0.0:
                score += (impact * 30) # Impact value ranges roughly from -1.0 to 1.5, adjusting the multiplier
        
        score = max(0, min(100, score))

        action = "HOLD"
        if score >= buy_threshold:
            # Filtro EMA 200 de tendencia Macro
            if not ignore_ema and last_ema_200 is not None and last_price < last_ema_200:
                action = "HOLD"
                reason.append(f"COMPRA BLOQUEADA: Precio ({last_price:.2f}) < EMA 200 ({last_ema_200:.2f})")
            else:
                action = "BUY"
        elif score <= sell_threshold: # Ajustado umbral de venta para garantizar simetría
            action = "SELL"

        return {
            "score": score,
            "action": action,
            "reason": " | ".join(reason) if reason else "Mercado neutral",
            "rsi": float(last_rsi) if pd.notna(last_rsi) else None,
            "macd_hist": float(last_macd - last_signal) if (pd.notna(last_macd) and pd.notna(last_signal)) else None,
            "ema_200": float(last_ema_200) if pd.notna(last_ema_200) else None
        }

    def decide_action(self, analysis: dict, avg_buy_price: float, current_price: float, target_profit_perc: float, stop_loss_perc: float = -5.0, smart_dca_enabled: bool = False, current_bullets: int = 1, max_bullets: int = 3) -> str:
        """
        Decide la acción final considerando el margen de ganancia y la restricción de stop-loss.
        """
        if avg_buy_price > 0:
            # Calcular el porcentaje de beneficio actual de la posición
            profit_margin = ((current_price - avg_buy_price) / avg_buy_price) * 100
            
            # Regla 1: Comprobar condición de Stop-Loss (Prioridad Máxima)
            if profit_margin <= stop_loss_perc:
                analysis['reason'] += f" [STOP-LOSS DISPARADO: Margen {profit_margin:.2f}% <= {stop_loss_perc}%]"
                return "SELL"
            
            # Regla 2: Take-Profit (Prioridad sobre venta técnica)
            if profit_margin >= target_profit_perc:
                analysis['reason'] += f" [TAKE-PROFIT: Margen {profit_margin:.2f}% >= {target_profit_perc}%]"
                return "SELL"
                
            # Regla 3: Smart DCA
            if smart_dca_enabled and analysis['action'] == "BUY" and profit_margin <= -3.0 and current_bullets < max_bullets:
                analysis['reason'] += f" [SMART DCA: Bala {current_bullets + 1}/{max_bullets} | Margen {profit_margin:.2f}%]"
                return "BUY"
            
            # Regla 4: Si no se vende, informar siempre del margen en la interfaz
            if analysis['action'] == "SELL":
                analysis['reason'] += f" [Venta técnica bloqueada: Margen {profit_margin:.2f}% < {target_profit_perc}%]"
                return "HOLD"
            else:
                analysis['reason'] += f" [Posición Abierta: Margen {profit_margin:.2f}%]"
                return "HOLD" # Sobrescribimos el BUY interno a HOLD ya que no queremos promediar compras ahora mismo.
        else:
            # Si no tenemos posición, sólo respetamos las compras del análisis
            if analysis['action'] == "BUY":
                return "BUY"
            return "HOLD"

    def get_latest_news_headline(self, symbol):
        clean_symbol = symbol.replace("/USDC", "").replace("/USD", "").replace("-USD", "")
        import sqlite3
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                res = cursor.execute("SELECT id FROM assets WHERE symbol = ? OR symbol = ?", (symbol, clean_symbol)).fetchone()
                if not res: return None
                
                cursor.execute("""
                    SELECT headline, sentiment, impact_score 
                    FROM news_analysis 
                    WHERE asset_id = ? 
                    ORDER BY published_at DESC LIMIT 1
                """, (res[0],))
                row = cursor.fetchone()
                if row:
                    return f"Noticia: {row[0][:50]}... [{row[1].upper()} {row[2]}]"
        except Exception:
            pass
        return None
