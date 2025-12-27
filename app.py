from flask import Flask, render_template, request, redirect, url_for
from db import init_db, get_conn
import yfinance as yf
import pandas as pd

app = Flask(__name__)
init_db()

TICKER_ALIASES = {
    "VU": "VOO",
}


def canonicalize_ticker(raw: str) -> str:
    ticker = (raw or "").upper().strip()
    return TICKER_ALIASES.get(ticker, ticker)


def migrate_ticker_aliases():
    with get_conn() as conn:
        for old, new in TICKER_ALIASES.items():
            conn.execute("UPDATE trades SET ticker=? WHERE ticker=?", (new, old))
        conn.commit()


migrate_ticker_aliases()

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
    ticker = canonicalize_ticker(request.form.get("ticker"))
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
    ticker = canonicalize_ticker(ticker)
    if ticker != request.view_args["ticker"]:
        return redirect(url_for("ticker_page", ticker=ticker))

    with get_conn() as conn:
        trades = conn.execute(
            "SELECT id, buy_date, buy_price, qty FROM trades WHERE ticker=? ORDER BY buy_date",
            (ticker,),
        ).fetchall()

    t = yf.Ticker(ticker)
    hist = t.history(period="1y")  # можно поменять на "5y"
    if hist is None or hist.empty:
        return render_template("ticker.html", ticker=ticker, error="No price data found.", chart=None, trades=trades)

    hist = hist.reset_index()
    hist["Date"] = pd.to_datetime(hist["Date"]).dt.date.astype(str)

    chart_labels = hist["Date"].tolist()
    chart_prices = [float(x) for x in hist["Close"].tolist()]
    current_price = chart_prices[-1]

    buy_points = []
    price_by_date = dict(zip(chart_labels, chart_prices))
    for tr in trades:
        d = tr["buy_date"]
        y = price_by_date.get(d, None)
        # Если на эту дату нет торгов (выходной), покажем точку по buy_price
        buy_points.append({
            "x": d,
            "y": float(y) if y is not None else float(tr["buy_price"]),
            "label": f'Buy {ticker}: {tr["buy_price"]} x{tr["qty"]}'
        })

    return render_template(
        "ticker.html",
        ticker=ticker,
        chart={
            "labels": chart_labels,
            "prices": chart_prices,
            "current": current_price,
            "buys": buy_points,
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

    ticker = canonicalize_ticker(request.form.get("ticker"))
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
