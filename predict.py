# ============================================================
# STOCK MARKET PRO — predict.py
# XGBoost + Prophet ensemble.
#
#   python predict.py                      all stocks (slow, writes to the database)
#   python predict.py AAPL NVDA            only these stocks
#   python predict.py AAPL --dry-run       show the result, write nothing
#   python predict.py --evaluate-only      score past predictions and print accuracy
#
# Environment: PROPHET_WEIGHT (0 to 1, default 0.4) is Prophet's share of the ensemble.
#              0 turns Prophet off.
# ============================================================

import os
from dotenv import load_dotenv
load_dotenv()

import yfinance as yf
import mysql.connector
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
import json
import logging
import sys
import time

warnings.filterwarnings('ignore')
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
logging.getLogger("prophet").setLevel(logging.WARNING)

PROPHET_WEIGHT = min(max(float(os.getenv("PROPHET_WEIGHT", "0.4")), 0.0), 1.0)
EVAL_HORIZON_DAYS = 7        # trading days: what target_1w really predicts
EVAL_MIN_CALENDAR_DAYS = 11  # earliest a prediction can have 7 trading days of data after it

_warnings_shown = 0
MAX_WARNINGS = 30
CURRENT_TICKER = ""

def warn(msg):
    """Show a problem instead of hiding it. Capped so one bad run cannot flood the console."""
    global _warnings_shown
    _warnings_shown += 1
    if _warnings_shown <= MAX_WARNINGS:
        print(f"      ⚠️  {CURRENT_TICKER}: {msg}")
    elif _warnings_shown == MAX_WARNINGS + 1:
        print("      ⚠️  (further warnings suppressed)")

try:
    from prophet import Prophet
    PROPHET_OK = True
except Exception as e:
    PROPHET_OK = False
    print(f"Prophet unavailable: {e}")

try:
    from xgboost import XGBRegressor
    XGB_OK = True
except Exception as e:
    XGB_OK = False
    print(f"XGBoost unavailable (falling back to linear regression): {e}")

# Prophet / cmdstanpy reset their log level when imported, so quieten them again afterwards
logging.getLogger("cmdstanpy").setLevel(logging.WARNING)
logging.getLogger("prophet").setLevel(logging.WARNING)

from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LinearRegression

def get_db():
    config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", ""),
        "database": os.getenv("DB_NAME", "stock_market_pro_db")
    }
    if os.getenv("DB_SSL", "false").lower() == "true":
        config["ssl_disabled"] = False
    return mysql.connector.connect(**config)

def query(sql, params=None):
    conn = get_db()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(sql, params or [])
    result = cursor.fetchall()
    cursor.close(); conn.close()
    return result

LEGACY_COLUMNS = {
    "upper_1w": "FLOAT", "lower_1w": "FLOAT", "upper_1m": "FLOAT", "lower_1m": "FLOAT",
    "upper_3m": "FLOAT", "lower_3m": "FLOAT", "prob_up_1w": "FLOAT", "prob_up_1m": "FLOAT",
    "prob_up_3m": "FLOAT", "key_factors": "JSON", "model_used": "VARCHAR(50)",
    "price_history": "JSON", "predicted_path": "JSON", "run_date": "DATE",
    "actual_1w": "FLOAT", "actual_1w_date": "DATE", "direction_correct_1w": "TINYINT",
}

LOG_TABLE_SQL = """
    CREATE TABLE IF NOT EXISTS stocks_prediction_log (
        ticker VARCHAR(20) NOT NULL,
        run_date DATE NOT NULL,
        price_at_run FLOAT,
        target_1w FLOAT, target_1m FLOAT, target_3m FLOAT,
        buy_signal VARCHAR(20), momentum_score FLOAT, confidence_pct FLOAT, model_used VARCHAR(50),
        holdout_dir_acc FLOAT, holdout_up_rate FLOAT,
        eval_date DATE NULL, actual_price FLOAT NULL, direction_correct TINYINT NULL,
        PRIMARY KEY (ticker, run_date),
        KEY idx_eval (eval_date, run_date)
    )
"""

def setup_tables():
    """Make sure every column exists, and create the prediction log (one row per stock per run)."""
    conn = get_db(); cursor = conn.cursor()
    cursor.execute("SELECT COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'stocks_predictions'")
    existing = {r[0].lower() for r in cursor.fetchall()}
    for col, ddl in LEGACY_COLUMNS.items():
        if col not in existing:
            try:
                cursor.execute(f"ALTER TABLE stocks_predictions ADD COLUMN {col} {ddl}")
                print(f"  Added column stocks_predictions.{col}")
            except Exception as e:
                print(f"  ⚠️  Could not add column {col}: {e}")
    cursor.execute(LOG_TABLE_SQL)
    # Seed the log with the predictions already stored, so their accuracy can be measured too
    cursor.execute("""
        INSERT IGNORE INTO stocks_prediction_log
            (ticker, run_date, price_at_run, target_1w, target_1m, target_3m, buy_signal, momentum_score, confidence_pct, model_used)
        SELECT ticker, run_date, current_price, target_1w, target_1m, target_3m, buy_signal, momentum_score, confidence_pct, model_used
        FROM stocks_predictions
        WHERE run_date IS NOT NULL AND target_1w IS NOT NULL AND current_price IS NOT NULL
    """)
    conn.commit(); cursor.close(); conn.close()
    print("✅ Tables ready")

def calc_features(df):
    close = df['Close']; high = df['High']; low = df['Low']; vol = df['Volume']
    df['mom_7d']  = close.pct_change(7)  * 100
    df['mom_30d'] = close.pct_change(30) * 100
    df['mom_90d'] = close.pct_change(90) * 100
    df['ma20']  = close.rolling(20).mean()
    df['ma50']  = close.rolling(50).mean()
    df['ma200'] = close.rolling(200).mean()
    df['price_vs_ma50']  = (close - df['ma50'])  / df['ma50']  * 100
    df['price_vs_ma200'] = (close - df['ma200']) / df['ma200'] * 100
    delta = close.diff()
    gain  = delta.where(delta>0,0).rolling(14).mean()
    loss  = -delta.where(delta<0,0).rolling(14).mean()
    df['rsi'] = 100-(100/(1+gain/loss.replace(0,np.nan)))
    ema12 = close.ewm(span=12,adjust=False).mean()
    ema26 = close.ewm(span=26,adjust=False).mean()
    df['macd']      = ema12-ema26
    df['macd_sig']  = df['macd'].ewm(span=9,adjust=False).mean()
    df['macd_hist'] = df['macd']-df['macd_sig']
    ma20_std = close.rolling(20).std()
    df['bb_upper'] = df['ma20']+2*ma20_std
    df['bb_lower'] = df['ma20']-2*ma20_std
    df['bb_pos'] = (close-df['bb_lower'])/(df['bb_upper']-df['bb_lower'])
    df['vol_ma20']  = vol.rolling(20).mean()
    df['vol_ratio'] = vol/df['vol_ma20']
    df['volatility']= close.pct_change().rolling(20).std()*np.sqrt(252)*100
    df['atr_pct']   = pd.concat([high-low,(high-close.shift()).abs(),(low-close.shift()).abs()],axis=1).max(axis=1).rolling(14).mean()/close*100
    df['roc_5']  = close.pct_change(5)*100
    df['roc_20'] = close.pct_change(20)*100
    low14=low.rolling(14).min(); high14=high.rolling(14).max()
    df['stoch_k']=(close-low14)/(high14-low14)*100
    df['target_7d']  = close.shift(-7)/close-1
    df['target_30d'] = close.shift(-30)/close-1
    df['target_90d'] = close.shift(-90)/close-1
    return df

def get_db_feats(ticker):
    """Current fundamentals / analyst snapshot, used by the signal rules (upside, beta).
    Missing values stay None. They used to become 0, which made unknown-beta stocks look low risk."""
    try:
        r = query("""
            SELECT f.pe_ratio,f.forward_pe,f.eps,f.profit_margin,f.roe,f.debt_to_equity,f.beta,
                   a.target_price,a.upside_pct,a.num_analysts,
                   CASE a.analyst_rating WHEN 'strong_buy' THEN 5 WHEN 'buy' THEN 4
                       WHEN 'hold' THEN 3 WHEN 'sell' THEN 2 WHEN 'strong_sell' THEN 1 ELSE 3 END as analyst_score,
                   sm.institutional_ownership,sm.short_interest
            FROM stocks_live l
            LEFT JOIN stocks_fundamentals f ON l.ticker=f.ticker
            LEFT JOIN stocks_analyst a ON l.ticker=a.ticker
            LEFT JOIN stocks_smartmoney sm ON l.ticker=sm.ticker
            WHERE l.ticker=%s
        """, [ticker])
        if r:
            feats = {}
            for k, v in r[0].items():
                try: feats[k] = float(v) if v is not None else None
                except (TypeError, ValueError): feats[k] = None
            return feats
    except Exception as e:
        warn(f"fundamentals lookup failed: {e}")
    return {}

# The fundamentals / analyst numbers are a single current snapshot, so copying them onto five years of
# daily rows gives the model a constant column it cannot learn from. They feed the signal rules instead.
XGB_FEATURES = ['mom_7d','mom_30d','mom_90d','price_vs_ma50','price_vs_ma200',
                'rsi','macd','macd_hist','bb_pos','vol_ratio','volatility',
                'atr_pct','roc_5','roc_20','stoch_k']

def xgb_predict(df, db_feats, cur_px):
    results={}
    all_feats=[c for c in XGB_FEATURES if c in df.columns]
    for days,tcol in [(7,'target_7d'),(30,'target_30d'),(90,'target_90d')]:
        try:
            td=df[all_feats+[tcol]].dropna()
            if len(td)<100: continue
            X=td[all_feats].values; y=td[tcol].values
            split=int(len(X)*0.8)
            scaler=StandardScaler()
            Xtr=scaler.fit_transform(X[:split]); Xte=scaler.transform(X[split:])
            model=XGBRegressor(n_estimators=200,max_depth=4,learning_rate=0.05,
                               subsample=0.8,colsample_bytree=0.8,random_state=42,verbosity=0) if XGB_OK else LinearRegression()
            model.fit(Xtr,y[:split])
            latest=scaler.transform(df[all_feats].dropna().iloc[-1:].values)
            ret=float(model.predict(latest)[0])
            ret=max(min(ret,0.50),-0.50)
            yp=model.predict(Xte); yt=y[split:]
            dir_acc=sum((yp>0)==(yt>0))/len(yt)*100 if len(yt)>0 else 50
            up_rate=float((yt>0).mean()*100) if len(yt)>0 else 50.0  # what always guessing "up" would have scored
            conf=round(max(50,min(90,dir_acc)),1)
            results[days]={
                'target':round(cur_px*(1+ret),2),
                'return_pct':round(ret*100,2),
                'confidence':conf,
                'prob_up':round(conf if ret>0 else 100-conf,1),
                'dir_acc':round(float(dir_acc),1),
                'up_rate':round(up_rate,1)
            }
        except Exception as e:
            warn(f"XGBoost {days}-day model failed: {e}")
    return results

def prophet_predict(df, cur_px):
    results={}
    if not PROPHET_OK: return results
    try:
        pdf=df[['Close']].copy().reset_index()
        pdf.columns=['ds','y']
        pdf['ds']=pd.to_datetime(pdf['ds']).dt.tz_localize(None)
        pdf=pdf.dropna()
        if len(pdf)<100: return results
        m=Prophet(daily_seasonality=False,weekly_seasonality=True,
                  yearly_seasonality=True,changepoint_prior_scale=0.05,
                  interval_width=0.80)
        logging.disable(logging.INFO)  # cmdstanpy prints two INFO lines per fit and resets its own log level
        try:
            m.fit(pdf)  # no verbose= argument: the installed Prophet/cmdstanpy rejects it
        finally:
            logging.disable(logging.NOTSET)
        today=pd.Timestamp.now().normalize()
        future=m.make_future_dataframe(periods=100,freq='B')
        fc=m.predict(future)
        for days in [7,30,90]:
            try:
                td=today+pd.offsets.BDay(days)
                row=fc[fc['ds']>=td].iloc[0]
                tp=round(float(row['yhat']),2)
                results[days]={
                    'target':tp,
                    'upper':round(float(row['yhat_upper']),2),
                    'lower':round(float(row['yhat_lower']),2),
                    'return_pct':round((tp-cur_px)/cur_px*100,2),
                    'prob_up':70 if tp>cur_px else 30
                }
            except Exception as e:
                warn(f"Prophet {days}-day forecast failed: {e}")
    except Exception as e:
        warn(f"Prophet failed: {e}")
    return results

def ensemble(xgb, pro, cur_px):
    final={}
    for days in [7,30,90]:
        x=xgb.get(days); p=pro.get(days)
        if x and p:
            target=round(x['target']*(1-PROPHET_WEIGHT)+p['target']*PROPHET_WEIGHT,2)
            upper=p.get('upper',round(target*1.05,2))
            lower=p.get('lower',round(target*0.95,2))
            both_agree=(x['target']>cur_px)==(p['target']>cur_px)
            conf=round(min(90,x['confidence']+(10 if both_agree else -10)),1)
            prob=round((x['prob_up']+p['prob_up'])/2,1)
            model_used="XGBoost+Prophet"
        elif x:
            target=x['target']; upper=round(target*1.06,2); lower=round(target*0.94,2)
            conf=x['confidence']; prob=x['prob_up']; model_used="XGBoost"
        elif p:
            target=p['target']; upper=p.get('upper',round(target*1.06,2))
            lower=p.get('lower',round(target*0.94,2)); conf=58.0
            prob=p['prob_up']; model_used="Prophet"
        else: continue
        final[days]={'target':target,'upper':upper,'lower':lower,
                     'return_pct':round((target-cur_px)/cur_px*100,2),
                     'confidence':conf,'prob_up':prob,'model_used':model_used}
    return final

def get_signals(df, ens, cur_px, db_feats):
    try:
        latest=df.iloc[-1]
        ma50=float(latest.get('ma50',cur_px) or cur_px)
        ma200=float(latest.get('ma200',cur_px) or cur_px)
        if ma50>ma200 and cur_px>ma50: trend="Strong Uptrend"
        elif ma50>ma200: trend="Uptrend"
        elif ma50<ma200 and cur_px<ma50: trend="Strong Downtrend"
        elif ma50<ma200: trend="Downtrend"
        else: trend="Sideways"
        score=50.0
        rsi=float(latest.get('rsi',50) or 50)
        score+=(rsi-50)*0.3
        if float(latest.get('macd',0) or 0)>0: score+=10
        else: score-=10
        if cur_px>ma50: score+=8
        if cur_px>ma200: score+=7
        mom30=float(latest.get('mom_30d',0) or 0)
        score+=min(max(mom30*0.5,-10),10)
        upside=db_feats.get('upside_pct') or 0
        if upside>15: score+=8
        elif upside>5: score+=4
        elif upside<-10: score-=8
        score=round(max(0,min(100,score)),1)
        vol=float(latest.get('volatility',30) or 30)
        beta=db_feats.get('beta')
        if beta is None: beta=1.0  # unknown beta: judge by volatility alone
        if vol>60 or beta>1.8: risk="High"
        elif vol>35 or beta>1.2: risk="Medium"
        else: risk="Low"
        factors=[]
        if rsi<30: factors.append("RSI oversold — potential bounce")
        elif rsi>70: factors.append("RSI overbought — caution advised")
        else: factors.append(f"RSI neutral at {rsi:.1f}")
        if cur_px>ma50: factors.append("Price above 50-day MA — bullish signal")
        else: factors.append("Price below 50-day MA — bearish signal")
        if ma50>ma200: factors.append("Golden cross — long term bullish")
        else: factors.append("Death cross — long term bearish")
        if upside>10: factors.append(f"Analyst target {upside:.1f}% above current price")
        elif upside<-5: factors.append(f"Analyst target below current price")
        if float(latest.get('macd',0) or 0)>0: factors.append("MACD positive — momentum bullish")
        else: factors.append("MACD negative — momentum bearish")
        return trend, score, risk, factors[:5]
    except: return "Sideways",50.0,"Medium",["Insufficient data"]

def build_paths(hist, cur_px, ens):
    """Build price_history + predicted_path — timezone safe"""
    try:
        # Convert to timezone-naive using string comparison
        hist = hist.copy()
        hist.index = pd.to_datetime([str(x)[:10] for x in hist.index])
        one_year_ago = (datetime.now()-timedelta(days=365)).strftime("%Y-%m-%d")
        hist_1y = hist[hist.index >= pd.Timestamp(one_year_ago)]
        price_history=[{"date":str(idx)[:10],"price":round(float(row['Close']),2)}
                       for idx,row in hist_1y.iterrows()]
        today=datetime.now()
        predicted_path=[{"date":today.strftime("%Y-%m-%d"),"price":round(cur_px,2),
                         "upper":round(cur_px,2),"lower":round(cur_px,2),"predicted":False}]
        for days,label in [(7,"1W"),(30,"1M"),(90,"3M")]:
            if days in ens:
                e=ens[days]
                fd=today+timedelta(days=days)
                predicted_path.append({
                    "date":fd.strftime("%Y-%m-%d"),
                    "price":round(e['target'],2),
                    "upper":round(e['upper'],2),
                    "lower":round(e['lower'],2),
                    "return_pct":e['return_pct'],
                    "confidence":e['confidence'],
                    "label":label,"predicted":True
                })
        return price_history, predicted_path
    except Exception as e:
        print(f"      build_paths error: {e}")
        return [],[]

def save(ticker, cur_px, ens, trend, momentum, risk, factors, ph, pp, model_used, holdout=None):
    try:
        e7=ens.get(7,{}); e30=ens.get(30,{}); e90=ens.get(90,{})
        if momentum>=72: sig="Strong Buy"
        elif momentum>=58: sig="Buy"
        elif momentum>=42: sig="Hold"
        elif momentum>=28: sig="Sell"
        else: sig="Strong Sell"
        conn=get_db(); cursor=conn.cursor()
        cursor.execute("""
            INSERT INTO stocks_predictions
                (ticker,current_price,target_1w,target_1m,target_3m,
                 upper_1w,lower_1w,upper_1m,lower_1m,upper_3m,lower_3m,
                 prob_up_1w,prob_up_1m,prob_up_3m,buy_signal,trend,
                 momentum_score,risk_level,confidence_pct,key_factors,
                 model_used,price_history,predicted_path,run_date,last_updated)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                current_price=VALUES(current_price),
                target_1w=VALUES(target_1w),target_1m=VALUES(target_1m),target_3m=VALUES(target_3m),
                upper_1w=VALUES(upper_1w),lower_1w=VALUES(lower_1w),
                upper_1m=VALUES(upper_1m),lower_1m=VALUES(lower_1m),
                upper_3m=VALUES(upper_3m),lower_3m=VALUES(lower_3m),
                prob_up_1w=VALUES(prob_up_1w),prob_up_1m=VALUES(prob_up_1m),prob_up_3m=VALUES(prob_up_3m),
                buy_signal=VALUES(buy_signal),trend=VALUES(trend),
                momentum_score=VALUES(momentum_score),risk_level=VALUES(risk_level),
                confidence_pct=VALUES(confidence_pct),key_factors=VALUES(key_factors),
                model_used=VALUES(model_used),price_history=VALUES(price_history),
                predicted_path=VALUES(predicted_path),run_date=VALUES(run_date),
                last_updated=VALUES(last_updated),
                actual_1w=NULL,actual_1w_date=NULL,direction_correct_1w=NULL
        """, (ticker,cur_px,
              e7.get('target'),e30.get('target'),e90.get('target'),
              e7.get('upper'),e7.get('lower'),
              e30.get('upper'),e30.get('lower'),
              e90.get('upper'),e90.get('lower'),
              e7.get('prob_up'),e30.get('prob_up'),e90.get('prob_up'),
              sig,trend,momentum,risk,e30.get('confidence',60),
              json.dumps(factors),model_used,
              json.dumps(ph[-252:] if len(ph)>252 else ph),
              json.dumps(pp),datetime.now().date(),datetime.now()))
        holdout = holdout or {}
        cursor.execute("""
            INSERT INTO stocks_prediction_log
                (ticker,run_date,price_at_run,target_1w,target_1m,target_3m,buy_signal,momentum_score,
                 confidence_pct,model_used,holdout_dir_acc,holdout_up_rate)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                price_at_run=VALUES(price_at_run),target_1w=VALUES(target_1w),target_1m=VALUES(target_1m),
                target_3m=VALUES(target_3m),buy_signal=VALUES(buy_signal),momentum_score=VALUES(momentum_score),
                confidence_pct=VALUES(confidence_pct),model_used=VALUES(model_used),
                holdout_dir_acc=VALUES(holdout_dir_acc),holdout_up_rate=VALUES(holdout_up_rate),
                eval_date=NULL,actual_price=NULL,direction_correct=NULL
        """, (ticker,datetime.now().date(),cur_px,e7.get('target'),e30.get('target'),e90.get('target'),
              sig,momentum,e30.get('confidence',60),model_used,holdout.get('dir_acc'),holdout.get('up_rate')))
        conn.commit(); cursor.close(); conn.close()
        return True
    except Exception as e:
        print(f"    Save error: {e}")
        return False

def evaluate_outcome(hist, run_date, price_at_run, target_1w):
    """
    Score one past prediction: the close EVAL_HORIZON_DAYS trading days after run_date.
    Returns (actual_price, eval_date, direction_correct), or None if that many trading days have not passed.
    """
    if hist is None or len(hist) == 0:
        return None
    closes = pd.Series(hist['Close'].values, index=pd.to_datetime([str(x)[:10] for x in hist.index]))
    after = closes[closes.index > pd.Timestamp(run_date)].dropna()
    if len(after) < EVAL_HORIZON_DAYS:
        return None
    actual = float(after.iloc[EVAL_HORIZON_DAYS - 1])
    eval_date = after.index[EVAL_HORIZON_DAYS - 1].date()
    correct = int((target_1w > price_at_run) == (actual > price_at_run))
    return actual, eval_date, correct


def update_actuals():
    """Score every logged prediction that now has 7 trading days of data after it."""
    print("\n  📊 Scoring past predictions...")
    global CURRENT_TICKER
    try:
        due = query("""
            SELECT ticker, run_date, price_at_run, target_1w
            FROM stocks_prediction_log
            WHERE eval_date IS NULL AND target_1w IS NOT NULL AND price_at_run > 0
            AND run_date <= DATE_SUB(CURDATE(), INTERVAL %s DAY)
            ORDER BY ticker, run_date
        """, [EVAL_MIN_CALENDAR_DAYS])
        if not due:
            print("  Nothing is ready to score yet.")
            return
        by_ticker = {}
        for r in due:
            by_ticker.setdefault(r['ticker'], []).append(r)
        updates = []
        for i, (ticker, rows) in enumerate(by_ticker.items(), 1):
            CURRENT_TICKER = ticker
            try:
                first, last = min(r['run_date'] for r in rows), max(r['run_date'] for r in rows)
                hist = yf.Ticker(ticker).history(start=first - timedelta(days=1), end=last + timedelta(days=25))
                for r in rows:
                    out = evaluate_outcome(hist, r['run_date'], float(r['price_at_run']), float(r['target_1w']))
                    if out:
                        updates.append((out[0], out[1], out[2], ticker, r['run_date']))
            except Exception as e:
                warn(f"could not score: {e}")
            if i % 100 == 0:
                print(f"    scored {i}/{len(by_ticker)} stocks...")
        if updates:
            conn = get_db(); cursor = conn.cursor()
            cursor.executemany("""UPDATE stocks_prediction_log
                SET actual_price=%s, eval_date=%s, direction_correct=%s WHERE ticker=%s AND run_date=%s""", updates)
            conn.commit(); cursor.close(); conn.close()
        print(f"  ✅ Scored {len(updates)} of {len(due)} due predictions")
    except Exception as e:
        print(f"  Scoring error: {e}")


def accuracy_summary():
    """How the model has done against simply guessing 'up', from the scored predictions in the log."""
    try:
        r = query("""
            SELECT COUNT(*) n, SUM(direction_correct) hits,
                   AVG(actual_price > price_at_run)*100 up_rate,
                   MIN(run_date) first_run, MAX(run_date) last_run
            FROM stocks_prediction_log WHERE eval_date IS NOT NULL
        """)[0]
        n = int(r['n'] or 0)
        if not n:
            return {"scored": 0}
        hit = float(r['hits'] or 0) / n * 100
        up = float(r['up_rate'] or 0)
        return {"scored": n, "model_hit_pct": round(hit, 1), "always_up_pct": round(up, 1),
                "edge_pp": round(hit - up, 1), "first_run": str(r['first_run']), "last_run": str(r['last_run'])}
    except Exception as e:
        return {"scored": 0, "error": str(e)}


def print_accuracy():
    a = accuracy_summary()
    print("\n  📈 Accuracy of scored predictions (7 trading days ahead):")
    if not a.get("scored"):
        print("     none scored yet — run again after 11+ days" + (f" ({a['error']})" if a.get("error") else ""))
        return
    print(f"     {a['scored']} predictions | model right {a['model_hit_pct']}% | always guessing 'up' would be right {a['always_up_pct']}% "
          f"| edge {a['edge_pp']:+.1f} points")

def process(ticker, dry_run=False):
    global CURRENT_TICKER
    CURRENT_TICKER = ticker
    try:
        hist = yf.Ticker(ticker).history(period="5y")
        if hist.empty or len(hist)<200:
            return False,"Insufficient history"
        cur_px=float(hist['Close'].iloc[-1])
        if cur_px<=0: return False,"Invalid price"
        df=calc_features(hist.copy())
        db_feats=get_db_feats(ticker)
        xgb_res=xgb_predict(df,db_feats,cur_px)
        pro_res=prophet_predict(df,cur_px) if PROPHET_WEIGHT>0 else {}
        ens=ensemble(xgb_res,pro_res,cur_px)
        if not ens: return False,"No model output"
        trend,momentum,risk,factors=get_signals(df,ens,cur_px,db_feats)
        ph,pp=build_paths(hist,cur_px,ens)
        model_used=ens.get(30,{}).get('model_used','XGBoost')
        e30=ens.get(30,{})
        summary=f"${cur_px:.2f}→${e30.get('target','?')} ({e30.get('return_pct',0):+.1f}%) | {trend} | {e30.get('confidence',0):.0f}% conf"
        if dry_run:
            e7=ens.get(7,{})
            beta=db_feats.get('beta')
            return True,(f"{summary}\n        model={model_used} | 7d target ${e7.get('target','?')} ({e7.get('return_pct',0):+.1f}%) | risk={risk}"
                         f" (beta {beta if beta is not None else 'n/a'}) | momentum={momentum}"
                         f"\n        holdout 7d: model {xgb_res.get(7,{}).get('dir_acc','?')}% vs always-up {xgb_res.get(7,{}).get('up_rate','?')}%")
        holdout={k:xgb_res.get(7,{}).get(k) for k in ('dir_acc','up_rate')}
        saved=save(ticker,cur_px,ens,trend,momentum,risk,factors,ph,pp,model_used,holdout)
        if saved:
            return True,summary+f" | {model_used}",holdout
        return False,"Save failed"
    except Exception as e:
        return False,str(e)[:60]

def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    evaluate_only = "--evaluate-only" in args
    tickers = [a.upper() for a in args if not a.startswith("--")]

    print("="*65)
    print("  STOCK MARKET PRO — Predictive Model")
    print(f"  XGBoost: {'✅' if XGB_OK else '❌'}  Prophet: {'✅' if PROPHET_OK and PROPHET_WEIGHT>0 else '❌ (off)' if PROPHET_OK else '❌'}  (Prophet weight {PROPHET_WEIGHT})")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}" + ("  [DRY RUN: nothing is written]" if dry_run else ""))
    print("="*65)

    if evaluate_only:
        setup_tables()
        update_actuals()
        print_accuracy()
        return

    if not dry_run:
        setup_tables()
        update_actuals()          # score earlier predictions before they are replaced
        print_accuracy()

    if tickers:
        stocks=[{'ticker':t} for t in tickers]
    else:
        stocks=query("""SELECT ticker FROM stocks_live
            WHERE current_price IS NOT NULL AND current_price>0
            AND (sector!='Indices' OR sector IS NULL)
            ORDER BY market_cap DESC""")
    print(f"\n  Stocks to predict: {len(stocks)}")
    if len(stocks)>20:
        print(f"  Est. time: {len(stocks)*3//60}-{len(stocks)*8//60} mins (longer with Prophet on)\n")
    ok=0; fail=0; fail_list=[]; holdouts=[]
    for i,s in enumerate(stocks,1):
        t=s['ticker']
        res=process(t, dry_run=dry_run)
        success,msg=res[0],res[1]
        if success:
            ok+=1; print(f"  ✅ [{i:3d}/{len(stocks)}] {t:<8} {msg}")
            if len(res)>2 and res[2].get('dir_acc') is not None: holdouts.append(res[2])
        else:
            fail+=1; fail_list.append(t)
            print(f"  ❌ [{i:3d}/{len(stocks)}] {t:<8} {msg}")
        if i%50==0:
            print(f"\n  ⏳ {i}/{len(stocks)} | ✅{ok} ❌{fail} | Pausing 5s...\n")
            time.sleep(5)
    print(f"\n{'='*65}")
    print(f"  ✅ Predicted: {ok}  ❌ Failed: {fail}")
    if fail_list: print(f"  Failed: {', '.join(fail_list[:15])}")
    if holdouts:
        print(f"  Holdout check (7-day direction, last 20% of each stock's history): "
              f"model {np.mean([h['dir_acc'] for h in holdouts]):.1f}% vs always-up {np.mean([h['up_rate'] for h in holdouts]):.1f}%")
    print(f"  Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*65)

if __name__=="__main__":
    main()
