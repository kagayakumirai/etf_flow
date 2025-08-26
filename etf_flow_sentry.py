
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF Flow Sentry (Farside → Discord Embed)
- JST 09:00 / 12:00 / 15:00 / 18:00 の定期実行を想定
- FarsideのBitcoin ETF Flow表をスクレイピング
- 前日分が見つかった時だけ Discord Embed 通知（無ければ黙る）
"""

import os
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

FARSIDE_URL = "https://farside.co.uk/bitcoin-etf-flows/"

def fetch_flows():
    r = requests.get(FARSIDE_URL, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    table = soup.find("table")
    if not table:
        raise RuntimeError("Farside: table not found")

    head_tr = table.find("tr")
    headers = [th.get_text(strip=True) for th in head_tr.find_all("th")]
    required = {"Date", "ETF"}
    if not required.issubset(set(headers)):
        raise RuntimeError(f"Unexpected headers: {headers}")

    rows = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if not tds:
            continue
        cells = [td.get_text(strip=True) for td in tds]
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))

    # JST基準の前日キー（例: "26 Aug 2025"）
    today_jst = datetime.now(timezone.utc) + timedelta(hours=9)
    y_jst = today_jst - timedelta(days=1)
    day_key = y_jst.strftime("%d %b %Y")

    flow_keys = ["Flow (BTC)", "Flow BTC", "Flow"]

    flows = []
    net_btc = 0
    for row in rows:
        if row.get("Date") != day_key:
            continue
        etf = row.get("ETF", "")
        flow_str = None
        for k in flow_keys:
            if k in row:
                flow_str = row[k]
                break
        if flow_str is None:
            continue
        s = flow_str.replace(",", "").replace("−", "-").replace("—", "").strip()
        if s in ("", "-", "na", "n/a"):
            continue
        try:
            flow = int(float(s))
        except Exception:
            continue
        net_btc += flow
        flows.append((etf, flow))

    return day_key, flows, net_btc

def send_discord(yesterday, flows, net_btc, webhook):
    color = 0x2ecc71 if net_btc > 0 else 0xe74c3c if net_btc < 0 else 0x95a5a6
    fields = [{
        "name": etf,
        "value": f"{'🟢' if flow>0 else '🔴' if flow<0 else '⚪'} {flow:+,} BTC",
        "inline": True
    } for etf, flow in flows]

    if len(fields) > 12:
        for f in fields:
            f["inline"] = False

    embed = {
        "title": f"{yesterday} Bitcoin ETF Flows",
        "color": color,
        "fields": fields,
        "footer": {"text": f"Net: {net_btc:+,} BTC • Source: Farside"}
    }
    r = requests.post(webhook, json={"embeds": [embed]}, timeout=15)
    r.raise_for_status()

if __name__ == "__main__":
    import sys, traceback
    try:
        print("[boot] ETF Flow Sentry Playwright v2.3 (row-scan)")
        webhook = os.getenv("DISCORD_WEBHOOK")
        if not webhook:
            raise RuntimeError("DISCORD_WEBHOOK not set")

        day_key, flows, net, headers = parse_via_playwright_row()

        if flows:
            send_discord(day_key, flows, net, webhook)
            print(f"[ok] {day_key} items={len(flows)} net={net:+,.1f} $m")
        else:
            print("[info] No data yet (silent)")
    except Exception:
        traceback.print_exc()
        sys.exit(1)

