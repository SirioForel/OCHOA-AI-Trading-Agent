from fastapi import FastAPI, Request, Form, Depends, Header, HTTPException
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from authlib.integrations.starlette_client import OAuth
import uvicorn
import json
import os
import datetime
import glob
import database
from database import init_db
from broker_client import BrokerClient

def format_pem(key_str):
    key_str = key_str.replace("\\n", "")
    if "-----BEGIN EC PRIVATE KEY-----" in key_str:
        body = key_str.replace("-----BEGIN EC PRIVATE KEY-----", "").replace("-----END EC PRIVATE KEY-----", "")
        body = body.replace(" ", "").replace("\n", "").replace("\r", "")
        lines = [body[i:i+64] for i in range(0, len(body), 64)]
        return "-----BEGIN EC PRIVATE KEY-----\n" + "\n".join(lines) + "\n-----END EC PRIVATE KEY-----\n"
    return key_str

# Permitir HTTP para desarrollo (OAuth normalmente requiere HTTPS)
os.environ['AUTHLIB_INSECURE_TRANSPORT'] = '1'

app = FastAPI()

# Session middleware
app.add_middleware(SessionMiddleware, secret_key="super-secret-key-for-ochoa", session_cookie="ochoa_session")

os.makedirs("templates", exist_ok=True)
templates = Jinja2Templates(directory="templates")
templates.env.globals["format_datetime"] = lambda ts: datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S') if ts else "Desconocida"

os.makedirs("static", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# Google OAuth setup
oauth = OAuth()
oauth.register(
    name='google',
    client_id='711849345215-kgju2ng9sseuursaeg5r6h0llpgakgvl.apps.googleusercontent.com',
    client_secret='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',
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile'
    }
)

@app.on_event("startup")
async def startup_event():
    await init_db()

@app.get("/api/scout_live")
async def get_scout_live(request: Request):
    import os, json, re
    logs = []
    if os.path.exists("/home/soporte/ochoa/hacker_terminal.log"):
        with open("/home/soporte/ochoa/hacker_terminal.log", "r") as f:
            lines = f.readlines()
            logs = [line.strip() for line in lines[-20:] if line.strip()]
            
    bot_matrices = {}
    
    # Add Equity History & Live Profit for AJAX
    equity_history = None
    try:
        user = request.session.get('user')
        if not user:
            user = {'email': 'hackathon@ochoa.local', 'name': 'Ochoa Admin'}
        db_user = await database.get_or_create_user(user['email'], user.get('name', ''))
        db_bots = await database.get_user_bots(db_user['id'])
        
        # Calculate shared portfolio/equity once
        if db_bots:
            bot = db_bots[0]
            client = BrokerClient(bot['api_key'], bot['api_secret'], is_live=bot['is_live'])
            h = client.get_portfolio_history(period='1D', timeframe='5Min')
            if h:
                try:
                    import requests
                    base_url = "https://api.alpaca.markets" if bot['is_live'] else "https://paper-api.alpaca.markets"
                    headers = {"APCA-API-KEY-ID": bot['api_key'], "APCA-API-SECRET-KEY": bot['api_secret']}
                    acc = requests.get(f"{base_url}/v2/account", headers=headers, timeout=5).json()
                    actual_equity = float(acc.get('portfolio_value', 0))
                    last_equity = float(acc.get('last_equity', 0))
                    if h['data'] and len(h['data']) > 0:
                        last_h = h['data'][-1]
                        offset = actual_equity - last_h
                        h['data'] = [x + offset for x in h['data']]
                    h['base_value'] = last_equity

                except Exception as ex:
                    pass
                equity_history = h
                
            # Fetch live positions once
            live_positions = []
            try:
                import requests
                base_url = "https://api.alpaca.markets" if bot['is_live'] else "https://paper-api.alpaca.markets"
                headers = {"APCA-API-KEY-ID": bot['api_key'], "APCA-API-SECRET-KEY": bot['api_secret']}
                resp = requests.get(f"{base_url}/v2/positions", headers=headers, timeout=5)
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
                pass


        for bot in db_bots:
            bot_id = bot['id']
            matrix = {
                "sentiment": "Neutral",
                "rsi": "50.0",
                "verdict": "ESPERANDO",
                "symbol": "---",
                "sentiment_color": "#888",
                "rsi_color": "#888"
            }
            
            scout_file = f"/home/soporte/ochoa/scout_results_{bot_id}.json"
            if os.path.exists(scout_file):
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
                            
                            top = sorted(scout_data, key=lambda x: x.get("score", 50), reverse=True)[0]
                            matrix["symbol"] = top.get("symbol", "")
                            
                            reason = top.get("reason", "")
                            rsi_match = re.search(r'RSI.*?([\d\.]+)', reason)
                            if rsi_match:
                                val = float(rsi_match.group(1))
                                matrix["rsi"] = f"{val}"
                                matrix["rsi_color"] = "#ef4444" if val > 70 else ("#f59e0b" if val < 30 else "#10b981")
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
                except:
                    pass
            bot_matrices[bot_id] = matrix

    except Exception as e:
        print(f"Error AJAX history: {e}")

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
    
    # Render index.html with bots
    for bot in bots:
        # We don't need to compute everything here anymore because the dashboard loads it via AJAX!
        # Actually wait, index.html loops over bots, so it needs bot.profit.
        bot['profit'] = 0.0
        if bot.get('state_file') and os.path.exists(bot['state_file']):
            try:
                with open(bot['state_file'], "r") as f:
                    st = json.load(f)
                    for sym, asset in st.get("assets", {}).items():
                        bot['profit'] += asset.get("realized_profit", 0.0)
            except:
                pass

    
    bot_scout_results = {}
    for bot in bots:
        scout_file = f"/home/soporte/ochoa/scout_results_{bot['id']}.json"
        if os.path.exists(scout_file):
            try:
                with open(scout_file, "r") as f:
                    data = json.load(f)
                    if data:
                        bot_scout_results[bot['id']] = data
            except:
                pass

    import datetime
    now = datetime.datetime.now()
    minutes = (now.minute // 15 + 1) * 15
    next_cycle = now + datetime.timedelta(minutes=(minutes - now.minute))
    next_cycle_str = f"Esperando siguiente ciclo: {next_cycle.strftime('%H:%M')}"
    return templates.TemplateResponse(request=request, name="index.html", context={"request": request, "user": user, "bots": bots, "next_cycle_str": next_cycle_str, "bot_scout_results": bot_scout_results})

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
        
    state_file = bot['state_file']
    state = {"assets": {}}
    if state_file and os.path.exists(state_file):
        with open(state_file, "r") as f:
            try:
                state = json.load(f)
            except:
                pass
                
    total_profit = 0.0
    for sym, asset in state.get("assets", {}).items():
        total_profit += asset.get("realized_profit", 0.0)

    ml_stats = []
    live_positions = []
    available_portfolios = []
    
    try:
        import requests
        base_url = "https://api.alpaca.markets" if bot['is_live'] else "https://paper-api.alpaca.markets"
        headers = {"APCA-API-KEY-ID": bot['api_key'], "APCA-API-SECRET-KEY": bot['api_secret']}
        resp = requests.get(f"{base_url}/v2/positions", headers=headers, timeout=5)
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
        pass
    
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
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5001)
