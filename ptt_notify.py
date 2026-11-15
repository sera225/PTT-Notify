#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PTT new-post notifications (Supports Discord Webhook & Telegram)"""

import argparse
import datetime
import gzip
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

# ---------------------------------------------------------------- settings --

# 1. Discord Webhook 設定（或透過環境變數 DISCORD_WEBHOOK_URL 注入）
DISCORD_WEBHOOK_URL = ""

# 2. Telegram 設定（或透過環境變數 PTT_TELEGRAM_TOKEN / PTT_TELEGRAM_CHAT_ID 注入）
TELEGRAM_BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""

# 本機常駐巡視頻率（秒數）：預設 180 秒（3 分鐘）
CHECK_INTERVAL_SECONDS = 180

# ------------------------------------------------------------------- rules --

BOARDS = ["e-shopping", "Lifeismoney", "PC_Shopping", "Storage_Zone"]
FILTERED_BOARDS = {"PC_Shopping", "Storage_Zone"}
MAX_POSTS_PER_RUN = 20
MAX_SEEN = 400

TIER_B_KEYWORDS = [
    "標錯", "錯標", "标错", "標價錯誤", "價格錯誤", "價錢錯誤", "打錯價",
    "bug", "系統錯誤", "系統出包", "出包", "漏洞", "疑似", "破盤",
    "1折", "2折", "0元", "1元", "少一個0", "少打一個0", "黑五",
    "black friday", "黑色星期五"
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(SCRIPT_DIR, "ptt_seen.json")


def log(message):
    now = datetime.datetime.now().strftime("%H:%M:%S")
    print("[%s] %s" % (now, message), flush=True)


def clean_text(raw):
    text = re.sub(r"<[^>]+>", "", raw or "")
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def fetch_board(board):
    """抓取 PTT 看板第一頁 HTML"""
    url = "https://www.ptt.cc/bbs/%s/index.html" % board
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Cookie": "over18=1",
            "Accept-Encoding": "gzip",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            raw = res.read()
            if res.headers.get("Content-Encoding") == "gzip":
                raw = gzip.decompress(raw)
            return raw.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        log("%s 抓取失敗: HTTP %s" % (board, exc.code))
    except Exception as exc:
        log("%s 抓取失敗: %s: %s" % (board, type(exc).__name__, exc))
    return None


def parse_board(board, content):
    """解析看板文章列表"""
    posts = []
    chunks = content.split('<div class="r-ent">')
    for chunk in chunks[1:]:
        title_match = re.search(r'<div class="title">(.*?)</div>', chunk, re.S)
        if not title_match:
            continue
        link_match = re.search(
            r'<a\s+href="([^"]+)">(.*?)</a>', title_match.group(1), re.S
        )
        if not link_match:
            continue
        href = link_match.group(1).strip()
        title = clean_text(link_match.group(2))
        if not href.startswith("/bbs/") or not title:
            continue
        author_match = re.search(r'<div class="author">(.*?)</div>', chunk, re.S)
        date_match = re.search(r'<div class="date">(.*?)</div>', chunk, re.S)
        nrec_match = re.search(r'<div class="nrec">(.*?)</div>', chunk, re.S)
        posts.append(
            {
                "key": board + "|" + href,
                "board": board,
                "title": title,
                "author": clean_text(author_match.group(1)) if author_match else "",
                "date": clean_text(date_match.group(1)) if date_match else "",
                "push": clean_text(nrec_match.group(1)) if nrec_match else "",
                "url": "https://www.ptt.cc" + href,
            }
        )
    return posts


def tier_of(title):
    lowered = title.lower()
    for kw in TIER_B_KEYWORDS:
        if kw.lower() in lowered:
            return "B"
    return "A"


def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return {"seen": [], "seeded": False}
    if not isinstance(state, dict):
        return {"seen": [], "seeded": False}
    seen = state.get("seen") or []
    if not isinstance(seen, list):
        seen = []
    return {"seen": seen, "seeded": bool(state.get("seeded"))}


def save_state(state):
    tmp_path = STATE_PATH + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    os.replace(tmp_path, STATE_PATH)


def send_discord(webhook_url, text):
    """發送 Discord Webhook 訊息"""
    if not webhook_url:
        return True, ""
    payload = {"content": text}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "PTT-Notifier/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return True, ""
    except Exception as exc:
        return False, "Discord 錯誤: %s" % exc


def send_telegram(token, chat_id, text):
    """發送 Telegram 訊息"""
    if not token or not chat_id:
        return True, ""
    url = "https://api.telegram.org/bot%s/sendMessage" % token
    payload = {"chat_id": chat_id, "text": text}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "PTT-Notifier/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            return True, ""
    except Exception as exc:
        return False, "Telegram 錯誤: %s" % exc


def broadcast(post, tier, dc_url, tg_token, tg_chat, part=None):
    """發送訊息至已啟用的通知管道"""
    # 格式化訊息
    if tier == "B":
        head_dc = "🚨 **錯價/神券警報**【%s】%s" % (post["board"], post["title"])
        head_tg = "🚨 錯價警報【%s】%s" % (post["board"], post["title"])
        if part in (2, 3):
            head_dc += f"（第 {part} 則）"
            head_tg += f"（第 {part} 則）"
    else:
        head_dc = "📢 **【%s】** %s" % (post["board"], post["title"])
        head_tg = "【%s】%s" % (post["board"], post["title"])

    meta = " ".join(p for p in [post["author"], post["date"]] if p)
    if post["push"]:
        meta = (meta + " | " if meta else "") + "推文: " + post["push"]

    msg_dc = "\n".join(filter(None, [head_dc, meta, post["url"]]))
    msg_tg = "\n".join(filter(None, [head_tg, meta, post["url"]]))

    if dc_url:
        ok, err = send_discord(dc_url, msg_dc)
        if not ok:
            log(err)

    if tg_token and tg_chat:
        ok, err = send_telegram(tg_token, tg_chat, msg_tg)
        if not ok:
            log(err)


def run_once(dc_url, tg_token, tg_chat):
    """檢查所有看板一次並送出通知"""
    state = load_state()
    seen_set = set(state["seen"])

    current_posts = []
    ordered_keys = []
    for board in BOARDS:
        content = fetch_board(board)
        if content is None:
            continue
        for post in parse_board(board, content):
            if post["key"] not in ordered_keys:
                ordered_keys.append(post["key"])
                current_posts.append(post)

    if not current_posts:
        log("未能從任何看板解析貼文，將於下次重試")
        return 0

    merged = list(dict.fromkeys(list(state["seen"]) + ordered_keys))
    new_state = {"seen": merged[-MAX_SEEN:], "seeded": True}

    if not state["seeded"] and not seen_set:
        save_state(new_state)
        log("第一次執行：已記錄現有 %d 篇貼文，下次檢查到新文時才會推播" % len(current_posts))
        return 0

    new_posts = [p for p in current_posts if p["key"] not in seen_set]
    save_state(new_state)

    if not new_posts:
        log("檢查完成，無新貼文（看板文章數: %d）" % len(current_posts))
        return 0

    sent = 0
    notified = 0
    for post in new_posts:
        tier = tier_of(post["title"])
        if post["board"] in FILTERED_BOARDS and tier != "B":
            continue
        if notified >= MAX_POSTS_PER_RUN:
            log("新貼文過多，本次僅通知前 %d 篇" % MAX_POSTS_PER_RUN)
            break
        notified += 1

        if tier == "B":
            # 錯價連發 3 則提醒避免漏看
            for part in (1, 2, 3):
                broadcast(post, tier, dc_url, tg_token, tg_chat, part)
                sent += 1
                if part < 3:
                    time.sleep(1.5)
        else:
            broadcast(post, tier, dc_url, tg_token, tg_chat)
            sent += 1
        time.sleep(1)

    log("發現 %d 篇新文，已送出 %d 則訊息" % (len(new_posts), sent))
    return sent


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="PTT 特價/錯價通知 Bot")
    parser.add_argument("--once", action="store_true", help="僅檢查一次後退出（適合 GitHub Actions）")
    parser.add_argument("--test", action="store_true", help="發送一則測試通知")
    args = parser.parse_args(argv)

    dc_url = (os.environ.get("DISCORD_WEBHOOK_URL") or DISCORD_WEBHOOK_URL).strip()
    tg_token = (os.environ.get("PTT_TELEGRAM_TOKEN") or TELEGRAM_BOT_TOKEN).strip()
    tg_chat = (os.environ.get("PTT_TELEGRAM_CHAT_ID") or TELEGRAM_CHAT_ID).strip()

    if not dc_url and (not tg_token or not tg_chat):
        print("【設定提醒】尚未設定通知管道！")
        print("請在 GitHub Secrets 或檔案中設定 DISCORD_WEBHOOK_URL 或 TELEGRAM 資訊。")
        return 2

    if args.test:
        test_msg = "🔔 【測試通知】PTT 價格監控通知服務已成功連線！"
        if dc_url:
            ok, err = send_discord(dc_url, test_msg)
            log("Discord 測試發送: %s" % ("成功" if ok else err))
        if tg_token and tg_chat:
            ok, err = send_telegram(tg_token, tg_chat, test_msg)
            log("Telegram 測試發送: %s" % ("成功" if ok else err))
        return 0

    if args.once:
        run_once(dc_url, tg_token, tg_chat)
        return 0

    log("PTT 監控中: %s（檢查間隔: %d 秒）" % (", ".join(BOARDS), CHECK_INTERVAL_SECONDS))
    try:
        while True:
            try:
                run_once(dc_url, tg_token, tg_chat)
            except Exception as exc:
                log("本次檢查出錯: %s: %s" % (type(exc).__name__, exc))
            time.sleep(CHECK_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        log("使用者中斷，停止監控。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
