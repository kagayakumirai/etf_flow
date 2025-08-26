#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF Flow Sentry (Farside → Discord Embed) - Playwright版
- JST 09/12/15/18 に実行
- 実ブラウザ(Chromium)でページを開いて最初のtableをスクレイピング
- 前日分が見つかった時だけ Discord Embed 通知（無ければ黙る）
"""
import os, math
from datetime import datetime, timezone, timedelta
from playwright.sync_api import sync_playwright
import requests
from bs4 import BeautifulSoup

URL = "https://farside.co.uk/bitcoin-etf-flows/"

def fetch_html_with_browser() -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="en-US")
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.goto(URL, wait_until="domcontentloaded")
        # 遅延読み込み対策で少し待つ
        page.wait_for_timeout(1500)
        html = page.content()
        browser.close()
        return html

def parse_flows(html: str):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return None, [], 0

    head_tr = table.find("tr")
    headers = [th.get_text(strip=True) for th in head_tr.find_all("th")]
    rows = []
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if not tds: 
            continue
        cells = [td.get_text(strip=True) for td in tds]
        if len(cells) != len(headers):
            continue
        rows.append(dict(zip(headers, cells)))

    # JST前日キー
    today_jst = datetime.now(timezone.utc) + timedelta(hours=9)
    y = today_jst - timedelta(days=1)
    day_key = y.strftime("%d %b %Y")

    flow_keys = ["Flow (BTC)", "Flow BTC", "Flow"]
    flows = []
    net = 0
    for r in rows:
        if r.get("Date") != day_key: 
            continue
        etf = r.get("ETF","")
        # 対応するFlow列
        fs = None
        for k in flow_keys:
            if k in r: fs = r[k]; break
        if fs is None: 
            continue
        s = fs.replace(",", "").replace("−","-").replace("—","").strip()
        if s in ("","-","na","n/a"): 
            continue
        try:
            val = int(float(s))
        except:
            continue
        net += val
        flows.append((etf, val))
    return day_key, flows, net

def send_discord(day_key, flows, net_btc, webhook):
    color = 0x2ecc71 if net_btc > 0 else 0xe74c3c if net_btc < 0 else 0x95a5a6
    fields = [{
        "name": etf,
        "value": f"{'🟢' if v>0 else '🔴' if v<0 else '⚪'} {v:+,} BTC",
        "inline": True
    } for etf, v in flows]
    if len(fields) > 12:
        for f in fields: f["inline"] = False
    embed = {
        "title": f"{day_key} Bitcoin ETF Flows",
        "color": color,
        "fields": fields,
        "footer": {"text": f"Net: {net_btc:+,} BTC • Source: Farside"}
    }
    r = requests.post(webhook, json={"embeds":[embed]}, timeout=20)
    r.raise_for_status()

if __name__ == "__main__":
    webhook = os.getenv("DISCORD_WEBHOOK")
    if not webhook:
        raise RuntimeError("DISCORD_WEBHOOK not set")
    html = fetch_html_with_browser()
    day_key, flows, net = parse_flows(html)
    if flows:
        send_discord(day_key, flows, net, webhook)
        print(f"[ok] {day_key} items={len(flows)} net={net:+,} BTC")
    else:
        print("[info] No data yet (silent)")
