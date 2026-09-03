import sqlite3
import json
import os
import sys
import logging
from datetime import datetime, timedelta
import requests
import subprocess

logging.basicConfig(
    level=logging.INFO, 
    format='[%(asctime)s] %(message)s', 
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/home/soporte/ochoa/hacker_terminal.log')
    ]
)
logger = logging.getLogger(__name__)

DB_PATH = '/home/soporte/ochoa/ochoa.db'
BASE_URL = 'https://paper-api.alpaca.markets'

def get_active_bots():
    if not os.path.exists(DB_PATH):
        logger.error(f"Database not found at {DB_PATH}")
        sys.exit(1)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, api_key, api_secret, base_trade_amount, state_file, asset_universe, take_profit_pct, risk_level FROM bots WHERE is_active = 1")
    rows = cursor.fetchall()
    conn.close()
    return rows

def manage_open_positions(bot_id, api_key, api_secret, state_file, asset_universe, take_profit_pct):
    url = f"{BASE_URL}/v2/positions"
    headers = {'APCA-API-KEY-ID': api_key, 'APCA-API-SECRET-KEY': api_secret}
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200:
            return
        positions = response.json()
    except:
        return

    TAKE_PROFIT_PCT = take_profit_pct / 100.0 if take_profit_pct else 1.00
    STOP_LOSS_PCT = -0.90

    for pos in positions:
        symbol = pos.get('symbol')
        if pos.get('asset_class') != 'us_option':
            continue
            
        qty = abs(float(pos.get('qty', 0)))
        unrealized_pl_pc = float(pos.get('unrealized_plpc', 0))
        
        if unrealized_pl_pc >= TAKE_PROFIT_PCT:
            logger.info(f"[{bot_id}] TAKE PROFIT TRIGGERED for {symbol}: {unrealized_pl_pc*100:.1f}%")
            execute_market_sell(api_key, api_secret, symbol, qty)
        elif unrealized_pl_pc <= -0.99:
            logger.info(f"[{bot_id}] Skipping Stop Loss for {symbol} because current Bid is 0 (No available quote).")
        elif unrealized_pl_pc <= STOP_LOSS_PCT:
            logger.info(f"[{bot_id}] STOP LOSS TRIGGERED for {symbol}: {unrealized_pl_pc*100:.1f}%")
            execute_market_sell(api_key, api_secret, symbol, qty)

def execute_market_sell(api_key, api_secret, symbol, qty):
    import subprocess
    cmd = ["alpaca", "order", "submit", "--symbol", symbol, "--qty", str(qty), "--side", "sell", "--type", "market", "--time-in-force", "day"]
    env = os.environ.copy()
    env['APCA_API_KEY_ID'] = api_key
    env['APCA_API_SECRET_KEY'] = api_secret
    env['APCA_API_BASE_URL'] = "https://paper-api.alpaca.markets"
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if res.returncode == 0:
        logger.info(f"Order placed successfully via CLI: {symbol}")
    else:
        logger.error(f"Failed to place order for {symbol} via CLI: {res.stderr}")


def get_target_assets(bot_id):
    target_file = f'/home/soporte/ochoa/scout_results_{bot_id}.json'
    if not os.path.exists(target_file):
        return []
        
    with open(target_file, 'r') as f:
        data = json.load(f)
        
    targets = []
    for item in data:
        score = item.get('score', 50)
        action = item.get('action', 'BUY')
        
        if action == 'BUY':
            targets.append((item, 'call'))
        elif action == 'SELL':
            targets.append((item, 'put'))
            
    return targets

def find_closest_atm_contract(api_key, api_secret, symbol, underlying_price, option_type, risk_level):
    headers = {'APCA-API-KEY-ID': api_key, 'APCA-API-SECRET-KEY': api_secret}
    url = f"{BASE_URL}/v2/options/contracts"
    params = {'underlying_symbols': symbol, 'status': 'active', 'limit': 1000}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        if response.status_code != 200:
            return None
        contracts = response.json().get('option_contracts', [])
    except:
        return None
        
    today = datetime.now().date()
    if risk_level == 'Kamikaze':
        min_date, max_date = today, today + timedelta(days=7)
    elif risk_level == 'Agresivo':
        min_date, max_date = today + timedelta(days=7), today + timedelta(days=14)
    else:
        min_date, max_date = today + timedelta(days=14), today + timedelta(days=30)
    
    valid = []
    for c in contracts:
        if c.get('type', '').lower() != option_type.lower():
            continue
        exp_str = c.get('expiration_date')
        if not exp_str: continue
        exp_date = datetime.strptime(exp_str, '%Y-%m-%d').date()
        if min_date <= exp_date <= max_date:
            valid.append(c)
            
    closest = None
    min_diff = float('inf')
    for c in valid:
        strike = float(c.get('strike_price', 0))
        diff = abs(strike - underlying_price)
        if diff < min_diff:
            min_diff = diff
            closest = c
    return closest

def execute_trade(api_key, api_secret, symbol, contract_symbol, budget):
    import subprocess
    cmd = ["alpaca", "order", "submit", "--symbol", contract_symbol, "--qty", "1", "--side", "buy", "--type", "market", "--time-in-force", "day"]
    env = os.environ.copy()
    env['APCA_API_KEY_ID'] = api_key
    env['APCA_API_SECRET_KEY'] = api_secret
    env['APCA_API_BASE_URL'] = "https://paper-api.alpaca.markets"
    res = subprocess.run(cmd, env=env, capture_output=True, text=True)
    if res.returncode == 0:
        logger.info(f"Order placed successfully via CLI: {contract_symbol}")
    else:
        logger.error(f"Failed to place order for {contract_symbol} via CLI: {res.stderr}")


def main():
    logger.info("Starting Ochoa Core...")
    bots = get_active_bots()
    for bot in bots:
        bot_id, api_key, api_secret, budget, state_file, asset_universe, take_profit_pct, risk_level = bot
        logger.info(f"Evaluating open positions for Bot {bot_id} (TP: {take_profit_pct}%)")
        manage_open_positions(bot_id, api_key, api_secret, state_file, asset_universe, take_profit_pct)
        
        targets = get_target_assets(bot_id)
        if not targets:
            continue
            
        url = f"{BASE_URL}/v2/positions"
        headers = {'APCA-API-KEY-ID': api_key, 'APCA-API-SECRET-KEY': api_secret}
        try:
            positions = requests.get(url, headers=headers).json()
        except:
            positions = []
            
        for asset, opt_type in targets:
            symbol = asset['symbol']
            price = asset.get('price', 0)
            score = asset.get('score', 0)
            
            already_owned = False
            for p in positions:
                if p.get('symbol', '').startswith(symbol):
                    pl_pc = float(p.get('unrealized_plpc', 0))
                    # Si es basura ilíquida o está muerta al -99%, no la consideramos "activa"
                    if pl_pc <= -0.99:
                        logger.info(f"[{bot_id}] Ignorando posición zombie de {symbol} para permitir nuevas compras.")
                        continue
                    already_owned = True
                    break
            
            if already_owned:
                continue
                
            contract = find_closest_atm_contract(api_key, api_secret, symbol, price, opt_type, risk_level)
            if contract:
                contract_symbol = contract.get('symbol')
                logger.info(f"[{bot_id}] Found closest ATM contract: {contract_symbol}")
                execute_trade(api_key, api_secret, symbol, contract_symbol, budget)

if __name__ == "__main__":
    main()
