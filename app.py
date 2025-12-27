from flask import Flask, render_template, request, redirect, url_for
from db import init_db, get_conn
import yfinance as yf
import pandas as pd

app = Flask(__name__)
init_db()


TIMEFRAMES = {
    "1d": {"period": "1d", "interval": "5m", "label": "1D"},
    "5d": {"period": "5d", "interval": "15m", "label": "5D"},
    "1mo": {"period": "1mo", "interval": "1h", "label": "1M"},
    "6mo": {"period": "6mo", "interval": "1d", "label": "6M"},
    "ytd": {"period": "ytd", "interval": "1d", "label": "YTD"},
    "1y": {"period": "1y", "interval": "1d", "label": "1Y"},
    "5y": {"period": "5y", "interval": "1wk", "label": "5Y"},
    "max": {"period": "max", "interval": "1mo", "label": "MAX"},
}

DEFAULT_TIMEFRAME = "1y"

@app.get("/")
def index():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT ticker, COUNT(*) as n, SUM(qty) as total_qty
            FROM trades
            GROUP BY ticker
            ORDER BY ticker
        """).fetchall()
    return render_template("index.html", tickers=rows)

@app.post("/trade")
def add_trade():
    ticker = (request.form.get("ticker") or "").upper().strip()
    buy_date = (request.form.get("buy_date") or "").strip()
    buy_price = float(request.form.get("buy_price") or 0)
    qty = float(request.form.get("qty") or 1)

    if not ticker or not buy_date or buy_price <= 0 or qty <= 0:
        return redirect(url_for("index"))

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO trades (ticker, buy_date, buy_price, qty) VALUES (?,?,?,?)",
            (ticker, buy_date, buy_price, qty),
        )
        conn.commit()
    return redirect(url_for("ticker_page", ticker=ticker))

@app.get("/ticker/<ticker>")
def ticker_page(ticker):
    ticker = ticker.upper().strip()

    timeframe = (request.args.get("range") or DEFAULT_TIMEFRAME).lower()
    if timeframe not in TIMEFRAMES:
        timeframe = DEFAULT_TIMEFRAME

    with get_conn() as conn:
        trades = conn.execute(
            "SELECT id, buy_date, buy_price, qty FROM trades WHERE ticker=? ORDER BY buy_date",
            (ticker,),
        ).fetchall()

    t = yf.Ticker(ticker)
    params = TIMEFRAMES[timeframe]
    try:
        hist = t.history(period=params["period"], interval=params["interval"])
    except Exception:
        hist = pd.DataFrame()

    if (hist is None or hist.empty) and timeframe != DEFAULT_TIMEFRAME:
        params = TIMEFRAMES[DEFAULT_TIMEFRAME]
        timeframe = DEFAULT_TIMEFRAME
        try:
            hist = t.history(period=params["period"], interval=params["interval"])
        except Exception:
            hist = pd.DataFrame()

    if hist is None or hist.empty:
        return render_template("ticker.html", ticker=ticker, error="No price data found.", chart=None, trades=trades)

    hist = hist.reset_index()
    date_series = pd.to_datetime(hist["Date"])
    if date_series.dt.tz is not None:
        date_series = date_series.dt.tz_convert(None)

    label_series = (
        date_series.dt.strftime("%Y-%m-%d %H:%M")
        if timeframe in ("1d", "5d", "1mo")
        else date_series.dt.date.astype(str)
    )
    date_keys = date_series.dt.date.astype(str)

    chart_labels = label_series.tolist()
    chart_prices = [float(x) for x in hist["Close"].tolist()]
    current_price = chart_prices[-1]

    buy_points = []
    price_by_date = dict(zip(date_keys, chart_prices))
    label_index_by_date = {d: idx for idx, d in enumerate(date_keys.tolist())}
    for tr in trades:
        d = tr["buy_date"]
        y = price_by_date.get(d, None)
        point_idx = label_index_by_date.get(d, len(chart_labels) - 1)
        # Если на эту дату нет торгов (выходной), покажем точку по buy_price
        buy_points.append({
            "x": chart_labels[point_idx],
            "y": float(y) if y is not None else float(tr["buy_price"]),
            "label": f'Buy {ticker}: {tr["buy_price"]} x{tr["qty"]}',
            "dateLabel": chart_labels[point_idx],
        })

    return render_template(
        "ticker.html",
        ticker=ticker,
        chart={
            "labels": chart_labels,
            "prices": chart_prices,
            "current": current_price,
            "buys": buy_points,
            "range": timeframe,
            "range_options": [(key, meta["label"]) for key, meta in TIMEFRAMES.items()],
        },
        trades=trades,
        error=None,
    )

@app.post("/delete/<int:trade_id>")
def delete_trade(trade_id):
    with get_conn() as conn:
        row = conn.execute("SELECT ticker FROM trades WHERE id=?", (trade_id,)).fetchone()
        if row:
            ticker = row["ticker"]
            conn.execute("DELETE FROM trades WHERE id=?", (trade_id,))
            conn.commit()
            return redirect(url_for("ticker_page", ticker=ticker))
    return redirect(url_for("index"))

@app.post("/trade/<int:trade_id>/update")
def update_trade(trade_id):
    with get_conn() as conn:
        row = conn.execute("SELECT ticker FROM trades WHERE id=?", (trade_id,)).fetchone()
    if not row:
        return redirect(url_for("index"))

    ticker = (request.form.get("ticker") or "").upper().strip()
    buy_date = (request.form.get("buy_date") or "").strip()
    buy_price = float(request.form.get("buy_price") or 0)
    qty = float(request.form.get("qty") or 0)

    if not ticker or not buy_date or buy_price <= 0 or qty <= 0:
        return redirect(url_for("ticker_page", ticker=row["ticker"]))

    with get_conn() as conn:
        conn.execute(
            "UPDATE trades SET ticker=?, buy_date=?, buy_price=?, qty=? WHERE id=?",
            (ticker, buy_date, buy_price, qty, trade_id),
        )
        conn.commit()

    return redirect(url_for("ticker_page", ticker=ticker))

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
