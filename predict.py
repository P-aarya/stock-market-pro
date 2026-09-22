# ============================================================
# STOCK MARKET PRO — predict.py (Updated)
# XGBoost + Prophet Ensemble
# Saves: predictions + accuracy tracking vs previous run
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
import time

warnings.filterwarnings('ignore')

try:
    from prophet import Prophet
    PROPHET_OK = True
except: PROPHET_OK = False

try:
    from xgboost import XGBRegressor
    XGB_OK = True
except: XGB_OK = False

from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LinearRegression

def get_db():
    config = {
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", 3306)),
        "user": os.getenv("DB_USER", "root"),
        "password": os.getenv("DB_PASSWORD", "Aroot092325"),
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

def setup_tables():
    """Ensure all required columns exist"""
    conn = get_db(); cursor = conn.cursor()
    cols = [
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS upper_1w FLOAT",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS lower_1w FLOAT",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS upper_1m FLOAT",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS lower_1m FLOAT",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS upper_3m FLOAT",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS lower_3m FLOAT",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS prob_up_1w FLOAT",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS prob_up_1m FLOAT",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS prob_up_3m FLOAT",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS key_factors JSON",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS model_used VARCHAR(50)",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS price_history JSON",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS predicted_path JSON",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS run_date DATE",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS actual_1w FLOAT",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS actual_1w_date DATE",
        "ALTER TABLE stocks_predictions ADD COLUMN IF NOT EXISTS direction_correct_1w TINYINT",
    ]
    for sql in cols:
        try: cursor.execute(sql)
        except: pass
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
    try:
        r = query("""
            SELECT f.pe_ratio,f.forward_pe,f.eps,f.profit_margin,f.roe,f.debt_to_equity,
                   a.target_price,a.upside_pct,a.num_analysts,
                   CASE a.analyst_rating WHEN 'strong_buy' THEN 5 WHEN 'buy' THEN 4
                       WHEN 'hold' THEN 3 WHEN 'sell' THEN 2 WHEN 'strong_sell' THEN 1 ELSE 3 END as analyst_score,
                   sm.institutional_ownership,sm.short_interest,sm.beta
            FROM stocks_live l
            LEFT JOIN stocks_fundamentals f ON l.ticker=f.ticker
            LEFT JOIN stocks_analyst a ON l.ticker=a.ticker
            LEFT JOIN stocks_smartmoney sm ON l.ticker=sm.ticker
            WHERE l.ticker=%s
        """, [ticker])
        if r:
            feats={}
            for k,v in r[0].items():
                try: feats[k]=float(v) if v is not None else 0.0
                except: feats[k]=0.0
            return feats
    except: pass
    return {}

def xgb_predict(df, db_feats, cur_px):
    results={}
    feat_cols=['mom_7d','mom_30d','mom_90d','price_vs_ma50','price_vs_ma200',
               'rsi','macd','macd_hist','bb_pos','vol_ratio','volatility',
               'atr_pct','roc_5','roc_20','stoch_k']
    db_cols=['pe_ratio','forward_pe','eps','profit_margin','roe',
             'analyst_score','upside_pct','institutional_ownership','short_interest','beta']
    for k,v in db_feats.items():
        if k in db_cols: df[k]=v
    all_feats=[c for c in feat_cols+db_cols if c in df.columns]
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
            conf=round(max(50,min(90,dir_acc)),1)
            results[days]={
                'target':round(cur_px*(1+ret),2),
                'return_pct':round(ret*100,2),
                'confidence':conf,
                'prob_up':round(conf if ret>0 else 100-conf,1)
            }
        except: pass
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
        m.fit(pdf,verbose=False)
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
            except: pass
    except: pass
    return results

def ensemble(xgb, pro, cur_px):
    final={}
    for days in [7,30,90]:
        x=xgb.get(days); p=pro.get(days)
        if x and p:
            target=round(x['target']*0.6+p['target']*0.4,2)
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
        upside=db_feats.get('upside_pct',0)
        if upside>15: score+=8
        elif upside>5: score+=4
        elif upside<-10: score-=8
        score=round(max(0,min(100,score)),1)
        vol=float(latest.get('volatility',30) or 30)
        beta=db_feats.get('beta',1.0)
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

def save(ticker, cur_px, ens, trend, momentum, risk, factors, ph, pp, model_used):
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
                last_updated=VALUES(last_updated)
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
        conn.commit(); cursor.close(); conn.close()
        return True
    except Exception as e:
        print(f"    Save error: {e}")
        return False

def update_actuals():
    """Update actual prices for predictions made ~1 week ago"""
    print("\n  📊 Updating actual prices for past predictions...")
    try:
        # Find predictions made ~7 days ago that don't have actuals yet
        old_preds = query("""
            SELECT ticker, target_1w, current_price, run_date
            FROM stocks_predictions
            WHERE run_date IS NOT NULL
            AND run_date <= DATE_SUB(CURDATE(), INTERVAL 6 DAY)
            AND actual_1w IS NULL
            AND target_1w IS NOT NULL
            LIMIT 200
        """)
        if not old_preds:
            print("  No predictions ready for accuracy check yet.")
            return
        updated=0
        for p in old_preds:
            try:
                r = query("SELECT current_price FROM stocks_live WHERE ticker=%s", [p['ticker']])
                if r and r[0]['current_price']:
                    actual = float(r[0]['current_price'])
                    pred   = float(p['target_1w'])
                    orig   = float(p['current_price'])
                    # Direction correct if both moved same way from original price
                    pred_up   = pred > orig
                    actual_up = actual > orig
                    correct   = 1 if pred_up == actual_up else 0
                    conn=get_db(); cursor=conn.cursor()
                    cursor.execute("""UPDATE stocks_predictions
                        SET actual_1w=%s, actual_1w_date=CURDATE(), direction_correct_1w=%s
                        WHERE ticker=%s AND run_date=%s
                    """, (actual, correct, p['ticker'], p['run_date']))
                    conn.commit(); cursor.close(); conn.close()
                    updated+=1
            except: pass
        print(f"  ✅ Updated actuals for {updated} stocks")
    except Exception as e:
        print(f"  Actuals update error: {e}")

def process(ticker):
    try:
        hist = yf.Ticker(ticker).history(period="5y")
        if hist.empty or len(hist)<200:
            return False,"Insufficient history"
        cur_px=float(hist['Close'].iloc[-1])
        if cur_px<=0: return False,"Invalid price"
        df=calc_features(hist.copy())
        db_feats=get_db_feats(ticker)
        xgb_res=xgb_predict(df,db_feats,cur_px)
        pro_res=prophet_predict(df,cur_px)
        ens=ensemble(xgb_res,pro_res,cur_px)
        if not ens: return False,"No model output"
        trend,momentum,risk,factors=get_signals(df,ens,cur_px,db_feats)
        ph,pp=build_paths(hist,cur_px,ens)
        model_used=ens.get(30,{}).get('model_used','XGBoost')
        saved=save(ticker,cur_px,ens,trend,momentum,risk,factors,ph,pp,model_used)
        if saved:
            e30=ens.get(30,{})
            return True,f"${cur_px:.2f}→${e30.get('target','?')} ({e30.get('return_pct',0):+.1f}%) | {trend} | {e30.get('confidence',0):.0f}% conf"
        return False,"Save failed"
    except Exception as e:
        return False,str(e)[:60]

def main():
    print("="*65)
    print("  STOCK MARKET PRO — Predictive Model (Updated)")
    print(f"  XGBoost: {'✅' if XGB_OK else '❌'}  Prophet: {'✅' if PROPHET_OK else '❌'}")
    print(f"  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*65)
    setup_tables()
    # First update actuals from previous run
    update_actuals()
    stocks=query("""SELECT ticker FROM stocks_live
        WHERE current_price IS NOT NULL AND current_price>0
        AND (sector!='Indices' OR sector IS NULL)
        ORDER BY market_cap DESC""")
    print(f"\n  Stocks to predict: {len(stocks)}")
    print(f"  Est. time: {len(stocks)*3//60}-{len(stocks)*5//60} mins\n")
    ok=0; fail=0; fail_list=[]
    for i,s in enumerate(stocks,1):
        t=s['ticker']
        success,msg=process(t)
        if success:
            ok+=1; print(f"  ✅ [{i:3d}/{len(stocks)}] {t:<8} {msg}")
        else:
            fail+=1; fail_list.append(t)
            print(f"  ❌ [{i:3d}/{len(stocks)}] {t:<8} {msg}")
        if i%50==0:
            print(f"\n  ⏳ {i}/{len(stocks)} | ✅{ok} ❌{fail} | Pausing 5s...\n")
            time.sleep(5)
    print(f"\n{'='*65}")
    print(f"  ✅ Predicted: {ok}  ❌ Failed: {fail}")
    if fail_list: print(f"  Failed: {', '.join(fail_list[:15])}")
    print(f"  Finished: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*65)

if __name__=="__main__":
    main()
