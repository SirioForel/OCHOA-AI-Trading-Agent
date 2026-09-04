# 🧠 OCHOA: LLM Options Sniper & Risk Guardian

**Built for the lablab.ai × Alpaca AI Trading Agents Hackathon 2026**

OCHOA is a fully autonomous, aggressive AI trading agent designed to trade Options on the S&P 500 and the "Magnificent 7". It replaces traditional rigid trading algorithms with a Large Language Model (LLM) that acts as a predictive brain, paired with a deterministic, hardcoded "Risk Guardian" that handles the actual capital allocation and order execution via the **Alpaca CLI**.

---

## 🚀 The Architecture: "The Brain & The Guardian"

Most autonomous trading agents make a critical mistake: they let the LLM directly execute trades and manage capital. During our development, we realized that giving an LLM direct access to liquidity via free-form tool-calling is a recipe for disaster (hallucinations, latency issues, and misunderstood API schemas). 

Our architecture elegantly separates concerns:

1. **The Brain (`predictive_brain.py` & `lobo_scout.py`):** 
   - Scans the market continuously.
   - Feeds live news and technical indicators (RSI, MACD) to the LLM.
   - The LLM's **only** job is to classify the opportunity and assign a `Brain Score` (0 to 100). It never touches the money.
   - If the score crosses the threshold (e.g., >65 for Calls, <35 for Puts), a signal is generated.

2. **The Guardian (`ochoa_core.py`):** 
   - A deterministic, hardcoded Python risk manager.
   - Intercepts the LLM's signals.
   - Checks available portfolio limits, applies Take Profit (e.g., 30%) and Stop Loss limits.
   - Handles the execution securely via **Alpaca CLI integration**.

---

## 🛠️ Key Features & "Build in Public" Milestones

Building OCHOA live during the hackathon led to several "Open-Heart Surgeries" on the code, resulting in these advanced features:

### 1. Alpaca CLI Native Integration
To comply strictly with the hackathon rules and ensure robust execution, OCHOA bypasses standard REST API wrappers for execution. All market orders (Buy/Sell) are executed by opening a subprocess and injecting commands directly into the **Alpaca CLI** (`alpaca order submit...`), ensuring zero translation errors between the LLM and the broker.

### 2. The "Zombie Options" Bypass
Options trading in a paper environment taught us a hard lesson about liquidity. Early on, OCHOA bought options with $0.01 premiums. Due to wide spreads and zero bids, they immediately marked a -100% unrealized P&L. The Guardian panicked, trying to market-sell them, resulting in API 403 Forbidden errors (no buyers).
**The Fix:** We implemented the "Zombie Bypass". If an option drops to -99% due to a $0 bid, the Guardian stops panicking, labels it a "Zombie", and ignores it—freeing up the portfolio slot so the Brain can buy a fresh, liquid contract for the same underlying asset.

### 3. Overdrive (Kamikaze) Mode
OCHOA features a dynamic dashboard where the user can flip the agent from "Conservative" to "Kamikaze" mode in real-time. In Kamikaze mode, OCHOA hunts for 0-7 DTE (Days to Expiration) contracts, aiming for aggressive short-term volatility breakouts. 

### 4. Dynamic Radar Multi-Bot System
OCHOA isn't just one bot. The `dashboard.py` (FastAPI) spawns multiple parallel agent instances. 
- **OCHOA S&P 500:** Scans the entire index.
- **OCHOA Mag 7:** A specialized sniper that purely analyzes the Magnificent 7, ignoring the noise of the broader market.

---

## 📈 The Results: Surviving Expiration Day

During the hackathon, OCHOA lived through its first massive Option Expiration Day (Sept 2nd). 
- We witnessed the brutal reality of OTM contracts expiring worthless.
- But we also saw the magic of AI prediction: OCHOA's CALL option on Apple (AAPL) ended "In the Money". Alpaca automatically exercised it, turning our option into 100 physical shares of Apple, locking in solid profit and boosting our equity!

---

## ⚙️ How to Run

### 🐳 The "Hackathon Judge" Way (Recommended)
The easiest way to run the entire OCHOA ecosystem (Dashboard, Scraper, and Trading Core) is using Docker:
```bash
docker-compose up --build
```
This will automatically:
- Install all dependencies and the **Alpaca CLI**.
- Expose the Dashboard on `http://localhost:5001`.
- Expose the Scraper API on `http://localhost:8000`.
- Run the Guardian Core (`ochoa_core.py`) every 15 minutes.

### 💻 The Manual Way
1. Clone this repository.
2. Install dependencies: `pip install -r requirements.txt`
3. Ensure you have the **Alpaca CLI** installed locally.
4. Run the dashboard: `uvicorn dashboard:app --host 0.0.0.0 --port 5001`
5. Run the scraper: `uvicorn scraper.main:app --host 0.0.0.0 --port 8000`
6. Run the trading core loop: `python3 ochoa_core.py` (Best set up as a cronjob running every 15 minutes).

*Built with ❤️, Python, FastAPI, and a lot of caffeine for the Alpaca Hackathon.*
