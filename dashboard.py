import os
import json
import logging
import datetime
import requests
import uvicorn
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.concurrency import run_in_threadpool
from authlib.integrations.starlette_client import OAuth

import database
from broker_client import BrokerClient

# --- Configuration & Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ochoa_dashboard")

BASE_DIR = Path(__file__).resolve().parent

if os.getenv("DEBUG", "False").lower() in ("true", "1", "yes"):
    os.environ['AUTHLIB_INSECURE_TRANSPORT'] = '1'

@asynccontextmanager
async def lifespan(app: FastAPI):
    await database.init_db()
    yield

app = FastAPI(lifespan=lifespan)

SESSION_SECRET = os.getenv("SESSION_SECRET", "change-me-in-production")
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, session_cookie="ochoa_session")

templates_dir = BASE_DIR / "templates"
static_dir = BASE_DIR / "static"
os.makedirs(templates_dir, exist_ok=True)
os.makedirs(static_dir, exist_ok=True)

templates = Jinja2Templates(directory=str(templates_dir))
templates.env.globals["format_datetime"] = lambda ts: datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else "Desconocida"

app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "TU_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "TU_CLIENT_SECRET")

oauth = OAuth()
oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)

# --- Endpoints ---

@app.get("/api/scout_live")
async def get_scout_live(request: Request):
    logs = []
    hacker_terminal_log = BASE_DIR / "hacker_terminal.log"
    if hacker_terminal_log.exists():
        try:
            with open(hacker_terminal_log, "r") as f:
                lines = f.readlines()
                logs = [line.strip() for line in lines[-20:] if line.strip()]
        except Exception as e:
            logger.error(f"Error reading terminal log: {e}")
            
    bot_matrices = {}
    equity_history = None
    live_positions = []
    
    try:
        user = request.session.get('user')
        if not user:
            user = {'email': 'hackathon@ochoa.local', 'name': 'Ochoa Admin'}
        db_user = await database.get_or_create_user(user['email'], user.get('name', ''))
        db_bots = await database.get_user_bots(db_user['id'])
        
        if db_bots:
            bot = db_bots[0]
            client = BrokerClient(bot['api_key'], bot['api_secret'], is_live=bot['is_live'])
            h = client.get_portfolio_history(period='1D', timeframe='5Min')
            
            if h:
                base_url = "https://api.alpaca.markets" if bot['is_live'] else "https://paper-api.alpaca.markets"
                headers = {"APCA-API-KEY-ID": bot['api_key'], "APCA-API-SECRET-KEY": bot['api_secret']}
                
                try:
                    acc_resp = await run_in_threadpool(requests.get, f"{base_url}/v2/account", headers=headers, timeout=5.0)
                    if acc_resp.status_code == 200:
                        acc = acc_resp.json()
                        actual_equity = float(acc.get('portfolio_value', 0))
                        last_equity = float(acc.get('last_equity', 0))
                        if h.get('data') and len(h['data']) > 0:
                            last_h = h['data'][-1]
                            offset = actual_equity - last_h
                            h['data'] = [x + offset for x in h['data']]
                        h['base_value'] = last_equity
                except Exception as ex:
                    logger.warning(f"Failed to fetch account for equity offset: {ex}")
                
                equity_history = h
                
            try:
                resp = await run_in_threadpool(requests.get, f"{base_url}/v2/positions", headers=headers, timeout=5.0)
                if resp.status_code == 200:
                    positions = resp.json()
                    for p in positions:
                        is_option = p['asset_class'] == 'us_option'
                        symbol = p['symbol']
                        unrealized_pl = float(p.get('unrealized_pl', 0.0))
                        unrealized_plpc = float(p.get('unrealized_plpc', 0.0)) * 100
                        delta = round(0.50 + (unrealized_plpc / 1000), 2)
                        live_positions.append({
                            "symbol": symbol, "is_option": is_option,
                            "unrealized_pl": unrealized_pl, "delta": delta
                        })
            except Exception as e:
                logger.warning(f"Failed to fetch live positions: {e}")

        for bot in db_bots:
            bot_id = bot['id']
            matrix = {
                "sentiment": "Neutral", "rsi": "50.0", "verdict": "ESPERANDO",
                "symbol": "---", "sentiment_color": "#888", "rsi_color": "#888"
            }
            
            scout_file = BASE_DIR / f"scout_results_{bot_id}.json"
            if scout_file.exists():
                try:
                    with open(scout_file, "r") as f:
                        scout_data = json.load(f)
                        if scout_data and len(scout_data) > 0:
                            verdicts = []
                            for item in scout_data:
                                score = float(item.get("score", 50))
                                action = item.get("action", "BUY")
                                symbol = item.get("symbol", "")
                                if score >= 70:
                                    verdicts.append(f"CALL {symbol}" if action != 'HOLD' else f"CALL {symbol} (BLOQ)")
                                elif score <= 30:
                                    verdicts.append(f"PUT {symbol}" if action != 'HOLD' else f"PUT {symbol} (BLOQ)")
                                    
                            matrix["verdict"] = " | ".join(verdicts) if verdicts else "ANALIZANDO MERCADO"
                            
                            top = scout_data[0]
                            matrix["symbol"] = top.get("symbol", "")
                            
                            last_rsi = top.get("rsi")
                            if last_rsi is not None:
                                last_rsi = float(last_rsi)
                                matrix["rsi"] = f"{last_rsi:.1f}"
                                if last_rsi < 35:
                                    matrix["rsi_color"] = "#10b981"
                                elif last_rsi > 65:
                                    matrix["rsi_color"] = "#ef4444"
                                else:
                                    matrix["rsi_color"] = "#f59e0b"
                            else:
                                matrix["rsi"] = "Mixto"
                                matrix["rsi_color"] = "#f59e0b"
                                
                            top_score = float(top.get("score", 50))
                            if top_score >= 70:
                                matrix["sentiment"] = f"{top_score}% Bullish"
                                matrix["sentiment_color"] = "#10b981"
                            elif top_score <= 30:
                                matrix["sentiment"] = f"{100-top_score}% Bearish"
                                matrix["sentiment_color"] = "#ef4444"
                            else:
                                matrix["sentiment"] = f"{top_score}% Mixto"
                except Exception as e:
                    logger.warning(f"Error parsing scout results for bot {bot_id}: {e}")
                    
            bot_matrices[bot_id] = matrix

    except Exception as e:
        logger.error(f"Error AJAX history: {e}")

    return {
        "logs": logs,
        "bot_matrices": bot_matrices,
        "equity_history": equity_history,
        "live_positions": live_positions
    }


@app.get("/")
async def read_root(request: Request):
    user = request.session.get('user')
    if not user:
        user = {'email': 'hackathon@ochoa.local', 'name': 'Ochoa Admin'}
        request.session['user'] = user
        
    db_user = await database.get_or_create_user(user['email'], user.get('name', ''))
    bots = await database.get_user_bots(db_user['id'])
    
    for bot in bots:
        bot['profit'] = 0.0
        if bot.get('state_file'):
            state_path = Path(bot['state_file'])
            if not state_path.is_absolute():
                state_path = BASE_DIR / state_path
                
            if state_path.exists():
                try:
                    with open(state_path, "r") as f:
                        st = json.load(f)
                        for sym, asset in st.get("assets", {}).items():
                            bot['profit'] += asset.get("realized_profit", 0.0)
                except Exception as e:
                    logger.warning(f"Error reading state file {state_path}: {e}")

    bot_scout_results = {}
    for bot in bots:
        scout_file = BASE_DIR / f"scout_results_{bot['id']}.json"
        if scout_file.exists():
            try:
                with open(scout_file, "r") as f:
                    data = json.load(f)
                    if data:
                        bot_scout_results[bot['id']] = data
            except Exception as e:
                logger.warning(f"Error reading scout file {scout_file}: {e}")

    now = datetime.datetime.now()
    minutes = (now.minute // 15 + 1) * 15
    next_cycle = now + datetime.timedelta(minutes=(minutes - now.minute))
    next_cycle_str = f"Esperando siguiente ciclo: {next_cycle.strftime('%H:%M')}"
    
    return templates.TemplateResponse(request=request, name="index.html", context={
        "request": request, 
        "user": user, 
        "bots": bots, 
        "next_cycle_str": next_cycle_str, 
        "bot_scout_results": bot_scout_results
    })


@app.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for('auth')
    return await oauth.google.authorize_redirect(request, redirect_uri)

@app.get("/auth")
async def auth(request: Request):
    token = await oauth.google.authorize_access_token(request)
    user = token.get('userinfo')
    if user:
        request.session['user'] = dict(user)
    return RedirectResponse(url='/')

@app.get("/logout")
async def logout(request: Request):
    request.session.pop('user', None)
    return RedirectResponse(url='/')


@app.get("/bot/{bot_id}")
async def bot_detail(request: Request, bot_id: int):
    user = request.session.get('user')
    if not user:
        return RedirectResponse(url='/')
        
    db_user = await database.get_or_create_user(user['email'])
    bot = await database.get_bot_by_id(bot_id, db_user['id'])
    
    if not bot:
        return RedirectResponse(url='/')
        
    state_file = bot.get('state_file')
    state = {"assets": {}}
    if state_file:
        state_path = Path(state_file)
        if not state_path.is_absolute():
            state_path = BASE_DIR / state_path
            
        if state_path.exists():
            try:
                with open(state_path, "r") as f:
                    state = json.load(f)
            except Exception as e:
                logger.warning(f"Failed to read state file for bot {bot_id}: {e}")
                
    total_profit = sum(asset.get("realized_profit", 0.0) for sym, asset in state.get("assets", {}).items())

    ml_stats = []
    live_positions = []
    available_portfolios = []
    
    try:
        base_url = "https://api.alpaca.markets" if bot['is_live'] else "https://paper-api.alpaca.markets"
        headers = {"APCA-API-KEY-ID": bot['api_key'], "APCA-API-SECRET-KEY": bot['api_secret']}
        
        resp = await run_in_threadpool(requests.get, f"{base_url}/v2/positions", headers=headers, timeout=5.0)
        if resp.status_code == 200:
            positions = resp.json()
            for p in positions:
                is_option = p['asset_class'] == 'us_option'
                symbol = p['symbol']
                unrealized_pl = float(p.get('unrealized_pl', 0.0))
                unrealized_plpc = float(p.get('unrealized_plpc', 0.0)) * 100
                delta = round(0.50 + (unrealized_plpc / 1000), 2)
                cost_basis = float(p.get('cost_basis', 0.0))
                live_positions.append({
                    "symbol": symbol, "is_option": is_option,
                    "unrealized_pl": unrealized_pl, "unrealized_plpc": unrealized_plpc,
                    "cost_basis": cost_basis, "delta": delta, "gamma": 0.04, "theta": -0.08
                })
    except Exception as e:
        logger.warning(f"Failed to fetch positions for bot {bot_id}: {e}")
    
    return templates.TemplateResponse(request=request, name="bot_detail.html", context={
        "request": request, 
        "user": user, 
        "bot": bot, 
        "state": state, 
        "total_profit": total_profit, 
        "ml_stats": ml_stats, 
        "portfolios": available_portfolios, 
        "live_positions": live_positions
    })


@app.post("/bot/{bot_id}/settings")
async def update_settings(
    request: Request, 
    bot_id: int, 
    risk_level: str = Form(...),
    smart_dca_enabled: str = Form(None),
    smart_dca_bullets: int = Form(3),
    base_trade_amount: float = Form(2.0),
    portfolio_id: str = Form(None),
    day_trader: str = Form(None),
    overdrive: str = Form(None),
    radar_size: int = Form(6),
    asset_universe: str = Form('SP500'),
    buy_threshold: int = Form(75),
    sell_threshold: int = Form(25),
    take_profit_pct: float = Form(100.0)
):
    user = request.session.get('user')
    if not user:
        return RedirectResponse(url='/', status_code=303)
        
    db_user = await database.get_or_create_user(user['email'])
    bot = await database.get_bot_by_id(bot_id, db_user['id'])
    
    if bot:
        if risk_level in ["Conservador", "Medio", "Agresivo", "Kamikaze"]:
            await database.update_bot_risk(bot_id, db_user['id'], risk_level)
            
        dca_enabled = bool(smart_dca_enabled == "on")
        dt_enabled = bool(day_trader == "on")
        od_enabled = bool(overdrive == "on")
        await database.update_bot_settings(bot_id, db_user['id'], dca_enabled, smart_dca_bullets, base_trade_amount, portfolio_id, dt_enabled, od_enabled, radar_size, asset_universe, buy_threshold, sell_threshold, take_profit_pct)
        
    return RedirectResponse(url=f'/bot/{bot_id}', status_code=303)


@app.post("/bot/{bot_id}/credentials")
async def update_credentials(
    request: Request,
    bot_id: int,
    exchange: str = Form(...),
    api_key: str = Form(...),
    api_secret: str = Form(...)
):
    user = request.session.get('user')
    if not user:
        return RedirectResponse(url='/', status_code=303)
        
    db_user = await database.get_or_create_user(user['email'])
    await database.update_bot_credentials(bot_id, db_user['id'], exchange, api_key, api_secret)
    return RedirectResponse(url=f'/bot/{bot_id}', status_code=303)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=5001)
