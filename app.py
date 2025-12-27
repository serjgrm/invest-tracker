from flask import Flask, render_template, request, redirect, url_for
from db import init_db, get_conn
import yfinance as yf
import pandas as pd

# Предзаполненные сделки, чтобы пользователь сразу видел свой портфель
DEFAULT_TRADES = [
    {"ticker": "NVDA", "buy_date": "2024-05-10", "buy_price": 905.0, "qty": 2},
    {"ticker": "NVDA", "buy_date": "2024-07-02", "buy_price": 125.0, "qty": 15},
    {"ticker": "NVDA", "buy_date": "2024-10-18", "buy_price": 118.0, "qty": 8},
    {"ticker": "VU", "buy_date": "2024-06-14", "buy_price": 95.0, "qty": 12},
    {"ticker": "VU", "buy_date": "2024-11-05", "buy_price": 102.5, "qty": 10},
    {"ticker": "VGT", "buy_date": "2024-05-21", "buy_price": 495.0, "qty": 1.3},
    {"ticker": "VGT", "buy_date": "2024-08-29", "buy_price": 525.0, "qty": 1.5},
]

app = Flask(__name__)
init_db()


def seed_default_trades():
    """Автоматически добавляет сделки по трем тикерам, если база пуста."""

    with get_conn() as conn:
        existing = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
        if existing:
            return

        conn.executemany(
            "INSERT INTO trades (ticker, buy_date, buy_price, qty) VALUES (?,?,?,?)",
            [(t["ticker"], t["buy_date"], t["buy_price"], t["qty"]) for t in DEFAULT_TRADES],
        )
        conn.commit()


seed_default_trades()


PERIOD_SETTINGS = {
    "1d": {"period": "1d", "interval": "30m", "label": "1 день"},
    "5d": {"period": "5d", "interval": "1h", "label": "5 дней"},
    "1mo": {"period": "1mo", "interval": "1d", "label": "1 месяц"},
    "6mo": {"period": "6mo", "interval": "1d", "label": "6 месяцев"},
    "1y": {"period": "1y", "interval": "1d", "label": "1 год"},
    "5y": {"period": "5y", "interval": "1wk", "label": "5 лет"},
}


def _fetch_history(ticker: str, period_key: str):
    settings = PERIOD_SETTINGS.get(period_key, PERIOD_SETTINGS["1y"])
    t = yf.Ticker(ticker)
    hist = t.history(period=settings["period"], interval=settings["interval"])  # type: ignore[arg-type]
    return hist.reset_index(), settings


def _normalize_dates(hist_df: pd.DataFrame):
    # Поддерживаем форматы с датой и временем (для 1d/5d) и только дату (для 1mo+)
    if "Datetime" in hist_df.columns:
        hist_df["DateLabel"] = pd.to_datetime(hist_df["Datetime"]).dt.strftime("%Y-%m-%d %H:%M")
    elif "Date" in hist_df.columns:
        hist_df["DateLabel"] = pd.to_datetime(hist_df["Date"]).dt.date.astype(str)
    else:
        hist_df["DateLabel"] = hist_df.index.astype(str)
    return hist_df


def _compute_buy_points(trades, hist_labels, chart_prices, ticker):
    price_by_label = dict(zip(hist_labels, chart_prices))

    # Дополнительно создаем мапу по дате без времени, чтобы точки попадали и на дневные интервалы
    price_by_date_only = {}
    for label, price in zip(hist_labels, chart_prices):
        date_part = label.split(" ")[0]  # "2024-01-01 11:00" -> "2024-01-01"
        price_by_date_only.setdefault(date_part, price)

    buy_points = []
    for tr in trades:
        d = tr["buy_date"]
        y = price_by_label.get(d) or price_by_date_only.get(d)
        buy_points.append({
            "x": d,
            "y": float(y) if y is not None else float(tr["buy_price"]),
            "label": f'Buy {ticker}: {tr["buy_price"]} x{tr["qty"]}'
        })
    return buy_points


def _get_current_price(ticker):
    t = yf.Ticker(ticker)
    price = t.history(period="5d")
    if price is None or price.empty:
        return None
    return float(price["Close"].iloc[-1])

@app.get("/")
def index():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT ticker, COUNT(*) as n, SUM(qty) as total_qty, SUM(qty * buy_price) as invested
            FROM trades
            GROUP BY ticker
            ORDER BY ticker
        """).fetchall()

    tickers_summary = []
    portfolio_invested = 0.0
    portfolio_value = 0.0

    for row in rows:
        invested = float(row["invested"] or 0)
        qty = float(row["total_qty"] or 0)
        current_price = _get_current_price(row["ticker"])
        market_value = qty * current_price if current_price is not None else None
        profit = market_value - invested if market_value is not None else None

        if market_value is not None:
            portfolio_value += market_value
        portfolio_invested += invested

        tickers_summary.append({
            "ticker": row["ticker"],
            "trades": row["n"],
            "qty": qty,
            "invested": invested,
            "current_price": current_price,
            "market_value": market_value,
            "profit": profit,
        })

    portfolio_profit = portfolio_value - portfolio_invested if rows else 0

    return render_template(
        "index.html",
        tickers=tickers_summary,
        portfolio={
            "invested": portfolio_invested,
            "value": portfolio_value,
            "profit": portfolio_profit,
        }
    )

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
    period_key = (request.args.get("period") or "1y").lower()
    if period_key not in PERIOD_SETTINGS:
        period_key = "1y"

    with get_conn() as conn:
        trades = conn.execute(
            "SELECT id, buy_date, buy_price, qty FROM trades WHERE ticker=? ORDER BY buy_date",
            (ticker,),
        ).fetchall()

    try:
        hist, settings = _fetch_history(ticker, period_key)
    except Exception as exc:  # noqa: BLE001
        return render_template(
            "ticker.html",
            ticker=ticker,
            error=f"Не удалось загрузить данные: {exc}",
            chart=None,
            trades=trades,
            period_key=period_key,
            period_settings=PERIOD_SETTINGS,
        )

    if hist is None or hist.empty:
        return render_template(
            "ticker.html",
            ticker=ticker,
            error="По этому периоду нет котировок.",
            chart=None,
            trades=trades,
            period_key=period_key,
            period_settings=PERIOD_SETTINGS,
        )

    hist = _normalize_dates(hist)

    chart_labels = hist["DateLabel"].tolist()
    chart_prices = [float(x) for x in hist["Close"].tolist()]
    current_price = chart_prices[-1]

    buy_points = _compute_buy_points(trades, chart_labels, chart_prices, ticker)

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
        period_key=period_key,
        period_settings=PERIOD_SETTINGS,
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
