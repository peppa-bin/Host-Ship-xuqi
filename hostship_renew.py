#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Host-Ship 自动续期 v2 — 架构对齐 katabump 成功方案：
真实品牌 Chrome/Edge 独立进程 + 远程调试端口 + CDP 附加 + CDP 鼠标点击过 Turnstile。
"""
import os
import re
import sys
import time
import json
import shutil
import socket

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests
from playwright.sync_api import sync_playwright

SERVER_URL = os.environ.get("SERVER_URL") or ""
HOSTSHIP_LOGIN = os.environ.get("HOSTSHIP_LOGIN") or ""
HOSTSHIP_PASSWORD = os.environ.get("HOSTSHIP_PASSWORD") or ""
TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN") or ""
TG_CHAT_ID = os.environ.get("TG_CHAT_ID") or ""
MANUAL_RUN = (os.environ.get("MANUAL_RUN") or "false").lower() == "true"
IS_PROXY = (os.environ.get("IS_PROXY") or "false").lower() == "true"
PROXY_SERVER = os.environ.get("PROXY_SERVER") or ""

DEBUG_PORT = 9222
CHROME_PATH = os.environ.get("CHROME_PATH") or ""


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
        log(f"⚠️ Telegram 通知发送异常: {e}")


def current_ip():
    for url in ("https://api.ipify.org", "https://ipv4.icanhazip.com"):
        try:
            r = requests.get(url, timeout=10)
            if r.status_code == 200:
                return r.text.strip()
        except Exception:
            continue
    return "unknown"


def find_browser_binary():
    """Locate a real branded chromium browser (Chrome or Edge)."""
    if CHROME_PATH and os.path.exists(CHROME_PATH):
        return CHROME_PATH
    candidates = [
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/opt/google/chrome/chrome",
        "C:/Program Files/Google/Chrome/Application/chrome.exe",
        "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
        os.path.expandvars(
            "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"
        ),
        os.path.expandvars(
            "C:/Program Files/Microsoft/Edge/Application/msedge.exe"
        ),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    which = shutil.which("google-chrome") or shutil.which("msedge")
    return which


def port_open(port):
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


browser_proc = [None]


def launch_browser():
    binary = find_browser_binary()
    if not binary:
        raise RuntimeError("未找到 Chrome/Edge 浏览器二进制")

    if port_open(DEBUG_PORT):
        log(f"端口 {DEBUG_PORT} 已有浏览器在运行，跳过启动")
        return

    args = [
        binary,
        f"--remote-debugging-port={DEBUG_PORT}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-gpu",
        "--window-size=1440,1000",
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--user-data-dir=/tmp/hostship-chrome-profile",
    ]
    if IS_PROXY and PROXY_SERVER:
        args.append(f"--proxy-server={PROXY_SERVER}")

    log(f"🚀 启动真实浏览器: {binary}")
    import subprocess
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    browser_proc[0] = proc
    time.sleep(5)


def solve_turnstile(page, max_rounds=12):
    """Click the visible Turnstile checkbox via CDP raw mouse events
    (trusted events) until a response token is issued."""
    for _ in range(10):
        try:
            if page.locator('input[name="cf-turnstile-response"]').count() > 0:
                break
        except Exception:
            pass
        time.sleep(1)

    log("🔐 检测到 Turnstile widget")
    client = page.context.new_cdp_session(page)

    def cdp_click(x, y):
        client.send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })
        time.sleep(0.05 + (int(time.time() * 1000) % 100) / 1000.0)
        client.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1,
        })

    def token():
        try:
            return page.evaluate(
                "() => { const i = document.querySelector("
                "'input[name=\"cf-turnstile-response\"]');"
                " return !!(i && i.value && i.value.length > 20); }"
            )
        except Exception:
            return False

    def rect():
        try:
            return page.evaluate(
                "() => {"
                " const input = document.querySelector('input[name=\"cf-turnstile-response\"]');"
                " if (!input) return null;"
                " const r0 = input.getBoundingClientRect();"
                " let el = input;"
                " for (let i = 0; i < 8 && el; i++) {"
                "  el = el.parentElement;"
                "  if (!el) break;"
                "  const r = el.getBoundingClientRect();"
                "  if (r.width > 150 && r.height > 40 && r.y > 0)"
                "   return {x: r.x, y: r.y, w: r.width, h: r.height};"
                " }"
                " return {x: r0.x, y: r0.y, w: 300, h: 65};"
                "}"
            )
        except Exception:
            return None

    for attempt in range(max_rounds):
        if token():
            log("✅ Turnstile 已通过")
            client.detach()
            return True

        r = rect()
        if not r:
            log(f"round {attempt + 1}: widget rect not found")
            time.sleep(12)
            continue

        x = r["x"] + 28
        y = r["y"] + r["h"] / 2
        log(f"🖱️ CDP 点击复选框 ({int(x)},{int(y)}) 第 {attempt + 1} 次")
        try:
            cdp_click(x, y)
        except Exception as e:
            log(f"⚠️ CDP click 异常: {e}")

        for i in range(40):
            time.sleep(1)
            if token():
                log(f"✅ Turnstile token 获取成功 ({i + 1}s)")
                try:
                    client.detach()
                except Exception:
                    pass
                return True

    try:
        client.detach()
    except Exception:
        pass
    return False



def safe_screenshot(page, path, full_page=True):
    try:
        page.screenshot(path=path, full_page=full_page, timeout=15000)
    except Exception as e:
        log(f'⚠️ 截图失败({path}): {e}')

def _read_alert(page):
    try:
        el = page.locator("div.alert").first
        el.wait_for(timeout=4000)
        return (el.inner_text() or "").strip()
    except Exception:
        return ""


def login_if_needed(page):
    log(f"🌐 打开 {SERVER_URL}")
    page.goto(SERVER_URL, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)

    body = (page.locator("body").inner_text() or "").lower()
    if "/server/" in page.url and "password" not in body:
        return True

    # solve interstitial turnstile if present
    solve_turnstile(page, max_rounds=6)

    log("等待登录表单...")
    try:
        page.wait_for_selector(
            'input[type="email"], input[name="email"]', timeout=45000
        )
    except Exception:
        pass

    email = None
    for sel in ('input[name="email"]', 'input[type="email"]'):
        try:
            el = page.locator(sel).first
            el.wait_for(timeout=5000)
            email = el
            break
        except Exception:
            continue
    password = None
    for sel in ('input[name="password"]', 'input[type="password"]'):
        try:
            el = page.locator(sel).first
            el.wait_for(timeout=5000)
            password = el
            break
        except Exception:
            continue

    if not email or not password:
        log(f"❌ 没找到登录框，当前页面: {page.url}")
        safe_screenshot(page, "hostship_login_fail.png", full_page=True)
        return False

    log("📧 填写邮箱...")
    email.fill(HOSTSHIP_LOGIN)
    time.sleep(1)
    log("🔑 填写密码...")
    password.fill(HOSTSHIP_PASSWORD)
    time.sleep(2)

    if not solve_turnstile(page, max_rounds=10):
        log("❌ Turnstile 未通过，无法登录")
        safe_screenshot(page, "hostship_turnstile_fail.png", full_page=True)
        return False

    log("🖱️ 点击提交按钮...")
    try:
        page.wait_for_selector(
            'button[type="submit"]:not([disabled]), '
            'button:has-text("Sign In"):not([disabled])', timeout=20000
        )
    except Exception:
        pass
    clicked = False
    for sel in ('button[type="submit"]', 'button:has-text("Sign In")', 'button:has-text("Login")'):
        try:
            page.locator(sel).first.click(timeout=8000)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        log('⚠️ 提交按钮点击失败')

    log("⏳ 等待登录跳转...")
    for _ in range(20):
        time.sleep(1)
        cur = page.url
        if "/server/" in cur:
            return True

    log(f"❌ 登录后未跳转到服务器页，当前: {page.url}")
    safe_screenshot(page, "hostship_login_fail.png", full_page=True)
    return False


def get_renewal_text(page):
    text = page.locator("body").inner_text()
    patterns = [
        r"Renewal\s+in\s+\d+\s+Days?",
        r"Renew\s+in\s+\d+\s+Days?",
        r"\d+\s+Days?\s+until\s+renewal",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(0)
    return ""


def find_renew_button(page):
    sels = [
        'button:has-text("Renew")',
        'a:has-text("Renew")',
        'button:has-text("续期")',
    ]
    for sel in sels:
        try:
            btn = page.locator(sel).first
            btn.wait_for(timeout=8000)
            return btn
        except Exception:
            continue
    return None


def wait_for_renewal_result(page, before):
    log("📋 等待续期结果...")
    for i in range(20):
        time.sleep(1)
        text = (page.locator("body").inner_text() or "")
        alert = _read_alert(page)
        combined = (alert or "") + " " + text[:4000]
        low = combined.lower()
        after = get_renewal_text(page)
        if "renew limit reached" in low or "can't renew" in low:
            return ("⏳ 未到续期时间", after or before, False)
        if any(k in low for k in ("success", "renewed", "extended")):
            return ("✅ 续期成功", after or before, True)
    return ("ℹ️ 未检测到明确结果", before, False)


def main():
    if not SERVER_URL.startswith("https://panel.host-ship.com/server/"):
        log("❌ SERVER_URL 不正确")
        tg("❌ Host-Ship 配置错误\nSERVER_URL 不是服务器详情页地址")
        return 1

    log("======================================")
    log(" Host-Ship Free Auto Renew v2")
    log("======================================")

    ip = current_ip()
    log(f"📍 当前出口IP：{ip}")

    exit_code = 1

    with sync_playwright() as p:
        launch_browser()
        browser = None
        for k in range(5):
            try:
                browser = p.chromium.connect_over_cdp(
                    f"http://127.0.0.1:{DEBUG_PORT}"
                )
                log("✅ CDP 连接成功")
                break
            except Exception as e:
                log(f"CDP 连接尝试 {k + 1} 失败: {e}")
                time.sleep(2)
        if not browser:
            tg("❌ Host-Ship 续期失败\n无法连接浏览器 CDP")
            return 1

        try:
            context = browser.contexts[0]
            page = context.pages[0] if context.pages else context.new_page()

            logged_in = False
            for attempt in range(3):
                result = login_if_needed(page)
                if result is True:
                    logged_in = True
                    break
                if attempt < 2:
                    log(f"🔁 登录第 {attempt + 1} 次失败，重试...")
                    time.sleep(5)

            if not logged_in:
                tg("❌ Host-Ship 登录失败\n登录失败或遇到安全验证")
                return 1

            log("✅ 登录成功")

            before = get_renewal_text(page)
            log(f"📅 当前续期状态：{before}")

            body = page.locator("body").inner_text()
            if re.search(r"Renew\s+Limit\s+Reached", body, re.I):
                log("⏳ 未到续期时间")
                if MANUAL_RUN:
                    tg(f"⏳ Host-Ship 未到续期时间\n{before or '未知'}")
                return 0

            btn = find_renew_button(page)
            if not btn:
                safe_screenshot(page, "hostship_no_renew_button.png", full_page=True)
                tg(f"⚠️ Host-Ship 需要检查\n没有找到 Renew 按钮；{before or '未知'}")
                return 1

            log("🖱️ 点击 Renew...")
            btn.click()
            time.sleep(3)

            # confirm modal if present
            try:
                modal_btn = page.locator(
                    'div.modal.show button, '
                    'div[class*="modal"] button'
                ).first
                modal_btn.wait_for(timeout=8000)
                modal_btn.click()
                log("✅ 已确认续期模态框")
            except Exception:
                log("ℹ️ 无模态框或直接执行")

            time.sleep(5)

            outcome, after, success = wait_for_renewal_result(page, before)
            safe_screenshot(page, "hostship_result.png", full_page=True)

            tg(f"{outcome}\n Host-Ship 续期\n📅 {before or '未知'} → {after or '未知'}")

            exit_code = 0 if success else 1

        finally:
            pass

    return exit_code


if __name__ == "__main__":
    import sys
    sys.exit(main())
