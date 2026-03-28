"""Daily email report sent at market close."""
from __future__ import annotations

import logging
import smtplib
import os
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def email_configured() -> bool:
    return bool(_env("GMAIL_APP_PASSWORD") and _env("REPORT_EMAIL", "rickyisanerd@gmail.com"))


def _compute_daily_pnl(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute aggregate P&L from open positions."""
    total_unrealized = 0.0
    total_market_value = 0.0
    total_cost_basis = 0.0
    position_details = []

    for p in positions:
        qty = float(p.get("qty", 0))
        entry = float(p.get("avg_entry_price", 0))
        current = float(p.get("current_price", entry))
        cost = qty * entry
        market_val = qty * current
        unrealized = market_val - cost
        pct = (unrealized / cost * 100) if cost > 0 else 0

        total_unrealized += unrealized
        total_market_value += market_val
        total_cost_basis += cost

        position_details.append({
            "symbol": p.get("symbol", "???"),
            "qty": qty,
            "entry": entry,
            "current": current,
            "unrealized": unrealized,
            "pct": pct,
        })

    total_pct = (total_unrealized / total_cost_basis * 100) if total_cost_basis > 0 else 0
    return {
        "total_unrealized": total_unrealized,
        "total_market_value": total_market_value,
        "total_cost_basis": total_cost_basis,
        "total_pct": total_pct,
        "positions": position_details,
    }


def _classify_trades(trades: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Split today's trades into buys, sells, and errors."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    buys, sells, errors = [], [], []
    for t in trades:
        created = str(t.get("created_at", ""))
        if today not in created:
            continue
        side = t.get("side", "")
        status = t.get("status", "")
        entry = {"symbol": t.get("symbol"), "qty": t.get("qty"), "price": t.get("price"), "status": status}
        if status == "error":
            errors.append(entry)
        elif side == "buy":
            buys.append(entry)
        elif side == "sell":
            sells.append(entry)
    return {"buys": buys, "sells": sells, "errors": errors}


def build_report_html(snapshot: Dict[str, Any]) -> str:
    """Build a clean HTML email from the dashboard snapshot."""
    account = snapshot.get("account", {})
    positions = snapshot.get("positions", [])
    trades = snapshot.get("trades", [])
    learning = snapshot.get("learning", {})

    cash = float(account.get("cash", 0))
    equity = float(account.get("equity", 0))
    buying_power = float(account.get("buying_power", 0))

    pnl = _compute_daily_pnl(positions)
    classified = _classify_trades(trades)
    today_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    # Position rows
    pos_rows = ""
    for p in pnl["positions"]:
        color = "#22c55e" if p["unrealized"] >= 0 else "#ef4444"
        arrow = "▲" if p["unrealized"] >= 0 else "▼"
        pos_rows += f"""
        <tr>
            <td style="padding:8px;border-bottom:1px solid #333;color:#e0e0e0"><strong>{p['symbol']}</strong></td>
            <td style="padding:8px;border-bottom:1px solid #333;color:#e0e0e0;text-align:right">{p['qty']:.0f}</td>
            <td style="padding:8px;border-bottom:1px solid #333;color:#e0e0e0;text-align:right">${p['entry']:.2f}</td>
            <td style="padding:8px;border-bottom:1px solid #333;color:#e0e0e0;text-align:right">${p['current']:.2f}</td>
            <td style="padding:8px;border-bottom:1px solid #333;color:{color};text-align:right;font-weight:bold">{arrow} ${p['unrealized']:+.2f} ({p['pct']:+.1f}%)</td>
        </tr>"""

    if not pos_rows:
        pos_rows = '<tr><td colspan="5" style="padding:12px;color:#888;text-align:center">No open positions</td></tr>'

    # Trade rows
    def _trade_rows(trade_list, label_color):
        if not trade_list:
            return f'<tr><td colspan="4" style="padding:8px;color:#888;text-align:center">None today</td></tr>'
        rows = ""
        for t in trade_list:
            price_str = f"${float(t.get('price', 0)):.2f}" if t.get('price') else "—"
            rows += f"""
            <tr>
                <td style="padding:6px;border-bottom:1px solid #333;color:#e0e0e0">{t['symbol']}</td>
                <td style="padding:6px;border-bottom:1px solid #333;color:#e0e0e0;text-align:right">{t.get('qty', '—')}</td>
                <td style="padding:6px;border-bottom:1px solid #333;color:#e0e0e0;text-align:right">{price_str}</td>
                <td style="padding:6px;border-bottom:1px solid #333;color:{label_color};text-align:right">{t['status']}</td>
            </tr>"""
        return rows

    # Learning weights
    weight_rows = ""
    for name, info in learning.items():
        w = float(info.get("weight", 1.0))
        wins = info.get("wins", 0)
        losses = info.get("losses", 0)
        bar_width = int(min(w / 3.0, 1.0) * 100)
        bar_color = "#22c55e" if w >= 1.0 else "#ef4444"
        weight_rows += f"""
        <tr>
            <td style="padding:6px;color:#e0e0e0">{name}</td>
            <td style="padding:6px;color:#e0e0e0;text-align:center">{wins}</td>
            <td style="padding:6px;color:#e0e0e0;text-align:center">{losses}</td>
            <td style="padding:6px;text-align:right">
                <span style="color:{bar_color};font-weight:bold">{w:.2f}</span>
                <div style="background:#333;border-radius:4px;height:6px;margin-top:2px">
                    <div style="background:{bar_color};border-radius:4px;height:6px;width:{bar_width}%"></div>
                </div>
            </td>
        </tr>"""

    if not weight_rows:
        weight_rows = '<tr><td colspan="4" style="padding:8px;color:#888;text-align:center">No learning data yet</td></tr>'

    # Net P&L banner
    net_color = "#22c55e" if pnl["total_unrealized"] >= 0 else "#ef4444"
    net_arrow = "▲" if pnl["total_unrealized"] >= 0 else "▼"

    html = f"""
    <div style="font-family:'Segoe UI',Arial,sans-serif;max-width:640px;margin:0 auto;background:#1a1a2e;color:#e0e0e0;border-radius:12px;overflow:hidden">
        <!-- Header -->
        <div style="background:linear-gradient(135deg,#0f0f23,#1a1a3e);padding:24px 28px;border-bottom:2px solid #333">
            <h1 style="margin:0;font-size:22px;color:#fff">🤖 TradeBot Daily Report</h1>
            <p style="margin:4px 0 0;color:#888;font-size:14px">{today_str} — Market Close Summary</p>
        </div>

        <!-- Net P&L Banner -->
        <div style="background:#0f0f23;padding:20px 28px;text-align:center;border-bottom:1px solid #333">
            <p style="margin:0;color:#888;font-size:12px;text-transform:uppercase;letter-spacing:1px">Net Unrealized P&L</p>
            <p style="margin:4px 0 0;font-size:32px;font-weight:bold;color:{net_color}">{net_arrow} ${pnl['total_unrealized']:+.2f}</p>
            <p style="margin:2px 0 0;color:{net_color};font-size:16px">({pnl['total_pct']:+.1f}%)</p>
        </div>

        <!-- Account Summary -->
        <div style="padding:20px 28px;border-bottom:1px solid #333">
            <h2 style="margin:0 0 12px;font-size:16px;color:#fff">💰 Account</h2>
            <table style="width:100%">
                <tr>
                    <td style="padding:4px 0;color:#888">Cash</td>
                    <td style="text-align:right;color:#e0e0e0;font-weight:bold">${cash:,.2f}</td>
                </tr>
                <tr>
                    <td style="padding:4px 0;color:#888">Equity</td>
                    <td style="text-align:right;color:#e0e0e0;font-weight:bold">${equity:,.2f}</td>
                </tr>
                <tr>
                    <td style="padding:4px 0;color:#888">Buying Power</td>
                    <td style="text-align:right;color:#e0e0e0;font-weight:bold">${buying_power:,.2f}</td>
                </tr>
                <tr>
                    <td style="padding:4px 0;color:#888">Open Positions</td>
                    <td style="text-align:right;color:#e0e0e0;font-weight:bold">{len(positions)}</td>
                </tr>
            </table>
        </div>

        <!-- Open Positions -->
        <div style="padding:20px 28px;border-bottom:1px solid #333">
            <h2 style="margin:0 0 12px;font-size:16px;color:#fff">📊 Open Positions</h2>
            <table style="width:100%;border-collapse:collapse">
                <tr style="border-bottom:2px solid #444">
                    <th style="padding:8px;text-align:left;color:#888;font-size:12px">SYMBOL</th>
                    <th style="padding:8px;text-align:right;color:#888;font-size:12px">QTY</th>
                    <th style="padding:8px;text-align:right;color:#888;font-size:12px">ENTRY</th>
                    <th style="padding:8px;text-align:right;color:#888;font-size:12px">CURRENT</th>
                    <th style="padding:8px;text-align:right;color:#888;font-size:12px">P&L</th>
                </tr>
                {pos_rows}
            </table>
        </div>

        <!-- Today's Trades -->
        <div style="padding:20px 28px;border-bottom:1px solid #333">
            <h2 style="margin:0 0 12px;font-size:16px;color:#fff">📈 Stocks Bought Today ({len(classified['buys'])})</h2>
            <table style="width:100%;border-collapse:collapse">
                {_trade_rows(classified['buys'], '#22c55e')}
            </table>
        </div>

        <div style="padding:20px 28px;border-bottom:1px solid #333">
            <h2 style="margin:0 0 12px;font-size:16px;color:#fff">📉 Stocks Sold Today ({len(classified['sells'])})</h2>
            <table style="width:100%;border-collapse:collapse">
                {_trade_rows(classified['sells'], '#f59e0b')}
            </table>
        </div>

        {f'''<div style="padding:20px 28px;border-bottom:1px solid #333">
            <h2 style="margin:0 0 12px;font-size:16px;color:#fff">⚠️ Errors ({len(classified['errors'])})</h2>
            <table style="width:100%;border-collapse:collapse">
                {_trade_rows(classified['errors'], '#ef4444')}
            </table>
        </div>''' if classified['errors'] else ''}

        <!-- Learning Weights -->
        <div style="padding:20px 28px;border-bottom:1px solid #333">
            <h2 style="margin:0 0 12px;font-size:16px;color:#fff">🧠 Learning Weights</h2>
            <table style="width:100%;border-collapse:collapse">
                <tr style="border-bottom:2px solid #444">
                    <th style="padding:6px;text-align:left;color:#888;font-size:12px">STRATEGY</th>
                    <th style="padding:6px;text-align:center;color:#888;font-size:12px">WINS</th>
                    <th style="padding:6px;text-align:center;color:#888;font-size:12px">LOSSES</th>
                    <th style="padding:6px;text-align:right;color:#888;font-size:12px">WEIGHT</th>
                </tr>
                {weight_rows}
            </table>
        </div>

        <!-- Footer -->
        <div style="padding:16px 28px;text-align:center;color:#555;font-size:11px">
            TradeBot MCP • Automated Daily Report • <a href="https://tradebot-production-fdac.up.railway.app/" style="color:#6366f1">Dashboard</a>
        </div>
    </div>
    """
    return html


def send_daily_report(snapshot: Dict[str, Any]) -> bool:
    """Send the daily market-close email. Returns True on success."""
    recipient = _env("REPORT_EMAIL", "rickyisanerd@gmail.com")
    sender = _env("REPORT_SENDER_EMAIL", recipient)
    password = _env("GMAIL_APP_PASSWORD")

    if not password:
        log.warning("GMAIL_APP_PASSWORD not set — skipping daily email report")
        return False

    html = build_report_html(snapshot)
    today_str = datetime.now(timezone.utc).strftime("%m/%d/%Y")

    pnl = _compute_daily_pnl(snapshot.get("positions", []))
    net = pnl["total_unrealized"]
    arrow = "📈" if net >= 0 else "📉"
    subject = f"{arrow} TradeBot {today_str}: ${net:+.2f} ({pnl['total_pct']:+.1f}%)"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    # Plain text fallback
    plain = f"TradeBot Daily Report — {today_str}\nNet Unrealized P&L: ${net:+.2f} ({pnl['total_pct']:+.1f}%)\nView dashboard: https://tradebot-production-fdac.up.railway.app/"
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    # Try SSL (465) first (more reliable on cloud platforms), then STARTTLS (587)
    errors = []
    for method, port in [("ssl", 465), ("starttls", 587)]:
        try:
            log.info(f"Attempting email via {method}:{port} to {recipient}...")
            if method == "ssl":
                with smtplib.SMTP_SSL("smtp.gmail.com", port, timeout=10) as server:
                    server.login(sender, password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP("smtp.gmail.com", port, timeout=10) as server:
                    server.starttls()
                    server.login(sender, password)
                    server.send_message(msg)
            log.info(f"Daily report emailed to {recipient} via {method}:{port}")
            return True
        except Exception as e:
            err_msg = f"{method}:{port} — {type(e).__name__}: {e}"
            log.warning(f"Email send failed: {err_msg}")
            errors.append(err_msg)
            continue

    log.error(f"Failed to send daily report — all methods failed: {errors}")
    return False
