#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Host-Ship 自动续期 v3 — 纯 HTTP 方案（无浏览器）。

原理：带登录 session cookie 调用面板客户端 API
  POST /api/client/servers/{id}/renew
即可续期。cookie 从 Secret HOSTSHIP_COOKIES 读取（Netscape 格式，
来自浏览器登录后导出，有效期以 jexactyl_session/remember_web 为准）。
"""
import os
import re
import sys
import time

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests

SERVER_URL = os.environ.get("SERVER_URL") or ""
COOKIES_TEXT = os.environ.get("HOSTSHIP_COOKIES") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""
TG_CHAT_ID = os.environ.get("TG_CHAT_ID") or ""
MANUAL_RUN = (os.environ.get("MANUAL_RUN") or "false").lower() == "true"
IS_PROXY = (os.environ.get("IS_PROXY") or "false").lower() == "true"
PROXY_SERVER = os.environ.get("PROXY_SERVER") or ""

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0.0.0 Safari/537.36")


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def server_id():
    m = re.search(r"/server/([a-z0-9]+)", SERVER_URL or "")
    return m.group(1) if m else ""


def tg(text):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("ℹ️ TG 未配置，跳过通知")
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT_ID, "text": text},
            timeout=10,
        )
        log("✅ Telegram 通知发送成功")
    except Exception as e:
        log(f"⚠️ Telegram 通知异常: {e}")


def parse_cookies(text):
    """Parse a Netscape cookie file into a dict name->value."""
    jar = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) != 7:
            continue
        name, value = parts[5], parts[6]
        jar[name] = value.replace("%3D", "=").replace("%2B", "+").replace("%2F", "/")
    return jar


def build_session():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    jar = parse_cookies(COOKIES_TEXT)
    for name, value in jar.items():
        domain = ".host-ship.com" if name == "cf_clearance" else "panel.host-ship.com"
        s.cookies.set(name, value, domain=domain, path="/")
    if IS_PROXY and PROXY_SERVER:
        s.proxies = {"http": PROXY_SERVER, "https": PROXY_SERVER}
    return s


def get_renewal_state(s, sid):
    r = s.get(
        f"https://panel.host-ship.com/api/client/servers/{sid}",
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"GET server API -> HTTP {r.status_code}")
    a = r.json()["attributes"]
    return {
        "expires": a.get("renewal_expires_at") or "未知",
        "renewable": a.get("renewable"),
        "cooldown": a.get("renewal_cooldown"),
        "locked": a.get("renewal_locked"),
    }


def main():
    sid = server_id()
    if not sid:
        log("❌ SERVER_URL 不正确（需要 .../server/<id>）")
        tg("❌ Host-Ship 配置错误\nSERVER_URL 缺少服务器 ID")
        return 1

    if not COOKIES_TEXT:
        log("❌ 缺少 HOSTSHIP_COOKIES secret")
        tg("❌ Host-Ship 续期失败\n缺少 HOSTSHIP_COOKIES secret")
        return 1

    log("======================================")
    log(" Host-Ship Auto Renew v3 (HTTP)")
    log("======================================")

    s = build_session()

    # 先访问一次面板首页刷新 XSRF cookie
    try:
        s.get("https://panel.host-ship.com/", timeout=30)
    except Exception as e:
        log(f"⚠️ 首页访问异常: {e}")

    sid = server_id()

    try:
        state = get_renewal_state(s, sid)
    except RuntimeError as e:
        log(f"❌ {e}（session 可能已过期，请更新 HOSTSHIP_COOKIES）")
        tg("❌ Host-Ship 续期失败\nsession 过期或 CF 拦截，请更新 HOSTSHIP_COOKIES")
        return 1

    log(f"📅 续期前状态：{state}")

    from urllib.parse import unquote
    xsrf = ""
    for c in s.cookies:
        if c.name == "XSRF-TOKEN":
            xsrf = unquote(c.value)
            break
    r = s.post(
        f"https://panel.host-ship.com/api/client/servers/{sid}/renew",
        headers={
            "X-XSRF-TOKEN": xsrf,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://panel.host-ship.com/server/" + sid,
        },
        timeout=30,
    )
    log(f"🖱️ POST renew -> HTTP {r.status_code}")

    if r.status_code == 204:
        state2 = get_renewal_state(s, sid)
        log(f"📅 续期后状态：{state2}")
        tg(
            f"✅ Host-Ship 续期成功\n"
            f"📅 {state['expires']} → {state2['expires']}"
        )
        return 0

    body = (r.text or "")[:300]
    low = body.lower()
    if r.status_code == 401:
        log("❌ 401 未认证：session 过期，请更新 HOSTSHIP_COOKIES")
        tg("❌ Host-Ship 续期失败\nsession 过期（401），请更新 HOSTSHIP_COOKIES")
        return 1

    if "more than 14 days" in low or "can't renew" in low or "unable" in low or "cooldown" in low:
        log(f"⏳ 已加满/未到续期时间：{body}")
        if MANUAL_RUN:
            tg(f"⏳ Host-Ship 无需续期（已加满或冷却中）\n{body}")
        return 0

    log(f"⚠️ 续期返回异常：HTTP {r.status_code} {body}")
    tg(f"⚠️ Host-Ship 续期异常\nHTTP {r.status_code} {body}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
