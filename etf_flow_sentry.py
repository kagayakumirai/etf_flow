
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF Flow Sentry (Farside → Discord Embed)
- JST 09/12/15/18 の定期実行
- Farside 403対策としてブラウザUA・リファラ・言語ヘッダを付与し、リトライを実装
- 前日分が見つかった時だけ Discord Embed 通知（無ければ黙る）
"""

import os
import time
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta

FARSIDE_URL = "https://farside.co.uk/bitcoin-etf-flows/"

BROWSER_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "en-US,en;q=0.9,ja;q=0.8",
    "referer": "https://www.google.com/",
    "cache-control": "no-cache",
}

def http_get_with_retry(url: str, tries: int = 3, sleep_sec: float = 1.5) -> requests.Response:
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, timeout=20, headers=BROWSER_HEADERS, allow_redirects=True)
            if r.status_code == 200:
                return r
            last = r
            time.sleep(sleep_sec)
        except Exception as e:
            last = e
            time.sleep(sleep_sec)
    if isinstance(last, requests.Response):
        last.raise_for_status()
    else:
        raise last

def fetch_flows():
    r = http_get_with_retry(FARSIDE_URL, tries=4, sleep_sec=2.0)
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
        # Flow列検出
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
    webhook = os.getenv("DISCORD_WEBHOOK")
    if not webhook:
        raise RuntimeError("DISCORD_WEBHOOK not set")

    yday, flows, net_btc = fetch_flows()
    if flows:
        send_discord(yday, flows, net_btc, webhook)
        print(f"[ok] Sent ETF flows for {yday} (items={len(flows)}, net={net_btc:+,} BTC)")
    else:
        print(f"[info] No ETF flow data for {yday} yet. (silent)")
