
#!/usr/bin/env 　python3
# -*- coding: utf-8 -*-
"""
ETF Flow Sentry (Farside → Discord Embed) - Playwright matrix parser (v2)
- 日付一致を「部分一致（contains）」に緩和して取りこぼしを防止
- DEBUG=1 環境変数で直近のDateセルをログ出力
"""

import os
import re
from datetime import datetime, timezone, timedelta
from typing import List, Tuple

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

URL = "https://farside.co.uk/bitcoin-etf-flows/"

TICKER_CANDIDATES = {"IBIT","FBTC","BITB","ARKB","BTCO","EZBC","BRRR","HODL","BTCW","GBTC","BTC"}

def _norm(s: str) -> str:
    return " ".join(s.replace("\xa0"," ").split()).strip()

def _parse_number(cell: str) -> float:
    s = _norm(cell).replace(",","")
    if s in {"", "-", "–", "—"}:
        return 0.0
    if s.startswith("(") and s.endswith(")"):
        try:
            return -float(s[1:-1])
        except:
            return 0.0
    try:
        return float(s)
    except:
        return 0.0

def fetch_html_with_browser() -> str:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(locale="en-US")
        page = ctx.new_page()
        page.set_default_timeout(30000)
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        html = page.content()
        browser.close()
        return html

def parse_matrix(html: str):
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not table:
        return None, [], 0.0, []

    trs = table.find_all("tr")
    header_idx = None
    headers = []

    # ティッカーの見出し行を探す
    for i, tr in enumerate(trs[:10]):
        ths = tr.find_all(["th","td"])
        texts = [_norm(th.get_text()) for th in ths]
        if any(t in TICKER_CANDIDATES for t in texts) and "Total" in texts:
            header_idx = i
            headers = texts
            break

    if header_idx is None or not headers:
        return None, [], 0.0, []

    # データ行
    data_rows = []
    for tr in trs[header_idx+1:]:
        tds = tr.find_all("td")
        if not tds:
            continue
        cells = [_norm(td.get_text()) for td in tds]
        if len(cells) != len(headers):
            continue
        data_rows.append(dict(zip(headers, cells)))

    # JST前日キー
    today_jst = datetime.now(timezone.utc) + timedelta(hours=9)
    y = today_jst - timedelta(days=1)
    day_key = y.strftime("%d %b %Y")

    # マッチング（部分一致）
    target = None
    sample_dates = []
    for row in data_rows:
        date_cell = _norm(row.get(headers[0], ""))
        if len(sample_dates) < 6:
            sample_dates.append(date_cell)
        if day_key.lower() in date_cell.lower():  # ← 部分一致
            target = row
            matched_date = date_cell
            break

    if not target:
        print("========== DEBUG START ==========")
        print(f"[debug] day_key = {day_key}")                # 例: "25 Aug 2025"
        print("[debug] headers:", headers)                   # 1列目がDateになっているか
        print("[debug] sample date cells:", " | ".join(sample_dates))  # テーブル先頭の数件
        print("=========== DEBUG END ===========")
        return day_key, [], 0.0, headers




    # 集計
    flows: List[Tuple[str, float]] = []
    net = 0.0
    for col in headers[1:-1]:
        val = _parse_number(target.get(col, "0"))
        flows.append((col, val))
        net += val

    if os.getenv("DEBUG") == "1":
        print(f"[debug] matched date cell: {matched_date}")
        print(f"[debug] computed net: {net}")

    return day_key, flows, net, headers

def send_discord(day_key: str, flows: List[Tuple[str,float]], net: float, webhook: str):
    color = 0x2ecc71 if net > 0 else 0xe74c3c if net < 0 else 0x95a5a6
    shown = [(k,v) for k,v in flows if abs(v) > 0.0] or flows[:2]

    fields = [{
        "name": k,
        "value": f"{'🟢' if v>0 else '🔴' if v<0 else '⚪'} {v:+,.1f} $m",
        "inline": True
    } for k,v in shown]

    embed = {
        "title": f"{day_key} Bitcoin ETF Net Flows ($m)",
        "color": color,
        "fields": fields,
        "footer": {"text": f"Net: {net:+,.1f} $m • Source: Farside"}
    }
    r = requests.post(webhook, json={"embeds":[embed]}, timeout=20)
    r.raise_for_status()

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
def parse_via_playwright_row():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="en-US",
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36")
        )
        page = ctx.new_page()
        page.set_default_timeout(45000)  # 45s に延長

        # 1) 厳しすぎた networkidle をやめて domcontentloaded に
        page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        # 少しだけ待つ（遅延読み込み対策）
        page.wait_for_timeout(1200)

        # 2) テーブルを待機（失敗したらフォールバックで続行）
        target_table = None
        try:
            # 「Fee」を含む表が現れるまで待機
            page.wait_for_selector("table:has-text('Fee') tr", timeout=15000)
            tables = page.locator("table")
            print(f"[debug] found {tables.count()} tables on page")
            for i in range(tables.count()):
                txt = tables.nth(i).inner_text()
                if "Fee" in txt and "IBIT" in txt:
                    target_table = tables.nth(i)
                    print(f"[debug] using table index {i}")
                    break
        except PWTimeout:
            print("[warn] table wait timed out; falling back to first <table>")

        # 3) フォールバック：最初の table を使ってみる
        if target_table is None:
            tables = page.locator("table")
            if tables.count() == 0:
                print("[error] no <table> found on page")
                browser.close()
                return day_key, [], 0.0, []
            target_table = tables.nth(0)
            print("[debug] fallback using first table")

        # 見出し行
        header_cells = target_table.locator("tr").nth(0).locator("th,td")
        headers = [" ".join(c.inner_text().replace("\xa0"," ").split()).strip()
                   for c in header_cells.all()]
        print("[debug] headers:", headers)

        rows = target_table.locator("tr")
        n = rows.count()
        print(f"[debug] rows in table: {n}")

        # 前日行を探す（部分一致）
        def _norm(s): return " ".join(s.replace("\xa0"," ").split()).strip()
        target_cells = None
        for i in range(1, n):
            cells = rows.nth(i).locator("td")
            if cells.count() == 0:
                continue
            date_text = _norm(cells.nth(0).inner_text())
            if day_key.lower() in date_text.lower():
                target_cells = [_norm(cells.nth(j).inner_text()) for j in range(cells.count())]
                print(f"[debug] matched row index {i}: date_cell='{date_text}'")
                break

        # 先頭数行のDateセルをダンプ（見つからない時の診断）
        if target_cells is None:
            sample = []
            for i in range(1, min(6, n)):
                try:
                    sample.append(_norm(rows.nth(i).locator('td').nth(0).inner_text()))
                except Exception:
                    pass
            print("[debug] sample date cells:", " | ".join(sample))

        browser.close()


        # 集計（左端=Date, 右端=Total）
        flows, net = [], 0.0
        for col, cell in zip(headers[1:-1], target_cells[1:-1]):
            v = _num(cell)
            flows.append((col, v))
            net += v
        return day_key, flows, net, headers


if __name__ == "__main__":
    import os, sys, traceback
    try:
        print("[boot] ETF Flow Sentry Playwright v2.4 (row-scan)")
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

