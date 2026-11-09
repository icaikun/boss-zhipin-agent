#!/usr/bin/env python3
"""
boss_cli.py — Raw CDP WebSocket CLI for BOSS Zhipin Job Hunting & Chat Assistant.

Designed for OpenAI Codex CLI, Claude Code, and Google Antigravity agents.
Bypasses Playwright/Selenium `Runtime.enable` detection by communicating directly
with a persistent Chromium/Edge profile over raw Chrome DevTools Protocol (CDP).
"""

import argparse
import asyncio
import datetime
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import aiohttp

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
EXAMPLE_CONFIG_PATH = os.path.join(BASE_DIR, "config.example.json")
HISTORY_PATH = os.path.join(BASE_DIR, "applied_history.json")

SYSTEM_DECLINE_SNIPPETS = [
    "很遗憾不能与您共事",
    "不要偷偷的看了不回复哦",
    "附件简历请求已发送",
    "对方已同意，您的附件简历已发送给对方",
]


def load_config(config_path=None):
    path = config_path or (DEFAULT_CONFIG_PATH if os.path.exists(DEFAULT_CONFIG_PATH) else EXAMPLE_CONFIG_PATH)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_history():
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"applied_companies": [], "applied_urls": []}


def save_history(history):
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def detect_browser_executable(cfg):
    exe = cfg.get("browser", {}).get("executable_path", "auto")
    if exe and exe != "auto" and os.path.exists(os.path.expanduser(exe)):
        return os.path.expanduser(exe)

    system = platform.system()
    candidates = []
    if system == "Windows":
        candidates = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    elif system == "Darwin":
        candidates = [
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
    else:
        for cmd in ["microsoft-edge", "google-chrome", "chromium", "chromium-browser"]:
            found = shutil.which(cmd)
            if found:
                return found

    for c in candidates:
        if os.path.exists(c):
            return c
    raise FileNotFoundError("Could not auto-detect Edge or Chrome executable. Please set browser.executable_path in config.json.")


def launch_browser(cfg, start_url="about:blank"):
    exe = detect_browser_executable(cfg)
    b_cfg = cfg.get("browser", {})
    user_data_dir = os.path.expanduser(b_cfg.get("user_data_dir", "~/.boss-edge-profile"))
    port = int(b_cfg.get("debug_port", 9222))
    win_size = b_cfg.get("window_size", "1280,900")

    proc = subprocess.Popen([
        exe,
        f"--remote-debugging-port={port}",
        "--remote-allow-origins=*",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        f"--window-size={win_size}",
        start_url,
    ])
    return proc, port


async def get_cdp_ws_url(port, url_Substr="zhipin.com", retries=20):
    for _ in range(retries):
        try:
            raw = urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=2).read().decode("utf-8")
            targets = json.loads(raw)
            pages = [t for t in targets if t.get("type") == "page"]
            matching = [t for t in pages if url_Substr in t.get("url", "")]
            chosen = matching[-1] if matching else (pages[-1] if pages else None)
            if chosen and chosen.get("webSocketDebuggerUrl"):
                return chosen["webSocketDebuggerUrl"]
        except Exception:
            pass
        await asyncio.sleep(0.35)
    raise RuntimeError(f"Failed to connect to CDP port {port}")


async def cdp_eval(ws, expr, msg_id):
    await ws.send_str(json.dumps({
        "id": msg_id,
        "method": "Runtime.evaluate",
        "params": {
            "expression": expr,
            "returnByValue": True,
            "awaitPromise": True,
        }
    }))
    while True:
        msg = await ws.receive()
        if msg.type == aiohttp.WSMsgType.TEXT:
            data = json.loads(msg.data)
            if data.get("id") == msg_id:
                return data.get("result", {}).get("result", {}).get("value")
        else:
            return None


async def cdp_click_xy(ws, x, y, msg_id):
    for ev_type in ["mouseMoved", "mousePressed", "mouseReleased"]:
        params = {"type": ev_type, "x": x, "y": y, "button": "left", "clickCount": 1}
        await ws.send_str(json.dumps({"id": msg_id, "method": "Input.dispatchMouseEvent", "params": params}))
        await ws.receive()
        msg_id += 1
        await asyncio.sleep(0.05)
    return msg_id


def decode_boss_font(s):
    """Decodes BOSS Zhipin private-use-area Unicode salary glyphs (\ue031-\ue03a -> 0-9)."""
    if not s:
        return ""
    return "".join(str(ord(ch) - 0xE031) if 0xE031 <= ord(ch) <= 0xE03A else ch for ch in s)


def salary_matches(sal_str, cfg):
    if "元/天" in sal_str or "元/时" in sal_str:
        return False
    m = re.search(r"(\d+)\s*-\s*(\d+)\s*K", sal_str, re.I)
    if not m:
        return False
    low, high = int(m.group(1)), int(m.group(2))
    t_cfg = cfg.get("target", {})
    overlap = t_cfg.get("salary_target_overlap", [20, 30])
    min_k = t_cfg.get("salary_min_k", 15)
    max_k = t_cfg.get("salary_max_k", 45)
    return high >= overlap[0] and low <= overlap[1] and low >= min_k and high <= max_k


def build_greeting(cfg, job_title, job_desc=""):
    cand = cfg.get("candidate", {})
    name = cand.get("display_name", "候选人")
    templates = cand.get("greeting_templates", {})
    dl = (job_title + " " + job_desc).lower()
    if any(k in dl for k in ["云原生", "k8s", "kubernetes", "容器", "基础架构", "infra"]) and "infra" in templates:
        tpl = templates["infra"]
    elif any(k in dl for k in ["全栈", "fullstack", "react", "typescript", "node"]) and "fullstack" in templates:
        tpl = templates["fullstack"]
    else:
        tpl = templates.get(
            "default",
            "您好，我是{display_name}的 AI 助手。看到贵司在招「{job_title}」，与他的项目经历非常契合，希望能有机会交流学习，期待您的回复！"
        )
    return tpl.format(display_name=name, job_title=job_title)


# ============================================================================
# Subcommands
# ============================================================================

async def cmd_login(cfg):
    print("[login] Launching browser with isolated profile. Please scan QR code on BOSS Zhipin...")
    proc, _ = launch_browser(cfg, "https://www.zhipin.com/web/geek/chat")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()


async def cmd_search(cfg, query_override=None, limit=10):
    history = load_history()
    applied_comps = set(history.get("applied_companies", []))
    applied_urls = set(history.get("applied_urls", []))

    t_cfg = cfg.get("target", {})
    f_cfg = cfg.get("filters", {})
    bad_comp = f_cfg.get("blocked_company_keywords", []) + f_cfg.get("blocked_industry_keywords", [])
    bad_title = [x.lower() for x in f_cfg.get("blocked_title_keywords", [])]
    bad_tags = f_cfg.get("blocked_degree_or_exp_tags", [])

    queries = [query_override] if query_override else t_cfg.get("search_keywords", ["后端开发工程师"])
    city = t_cfg.get("city_code", "101020100")
    job_type = t_cfg.get("job_type", "1901")
    exp = t_cfg.get("experience_codes", "104,105")
    scale = t_cfg.get("scale_codes", "302,303,304")

    proc, port = launch_browser(cfg, "about:blank")
    await asyncio.sleep(2.0)
    results = []
    try:
        ws_url = await get_cdp_ws_url(port, "")
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url) as ws:
                msg_id = 1
                for q in queries:
                    if len(results) >= limit:
                        break
                    q_enc = urllib.parse.quote(q)
                    url = f"https://www.zhipin.com/web/geek/jobs?city={city}&jobType={job_type}&experience={exp}&scale={scale}&query={q_enc}"
                    await cdp_eval(ws, f'location.href = "{url}"', msg_id); msg_id += 1
                    await asyncio.sleep(4.0)
                    cards = await cdp_eval(ws, '''(() => {
                        return Array.from(document.querySelectorAll('.job-card-box, .job-card-wrap')).map(el => ({
                            title: el.querySelector('.job-name, .job-title')?.innerText?.trim() || '',
                            salary: el.querySelector('.job-salary, .salary')?.innerText?.trim() || '',
                            tags: Array.from(el.querySelectorAll('.tag-list li')).map(x => x.innerText.trim()).join(' / '),
                            comp: el.querySelector('.boss-name, .company-name')?.innerText?.trim() || '',
                            href: (el.querySelector('a.job-name, a')?.href || '').split('?')[0]
                        }));
                    })()''', msg_id); msg_id += 1

                    for c in (cards or []):
                        comp = c.get("comp", "")
                        href = c.get("href", "")
                        title = decode_boss_font(c.get("title", "")).split("\n")[0].strip()
                        sal = decode_boss_font(c.get("salary", ""))
                        tags = decode_boss_font(c.get("tags", ""))
                        if not comp or not href or comp in applied_comps or href in applied_urls:
                            continue
                        if any(b in comp for b in bad_comp) or any(b in title.lower() for b in bad_title):
                            continue
                        if not salary_matches(sal, cfg) or any(t in tags for t in bad_tags):
                            continue
                        results.append({
                            "company": comp,
                            "title": title,
                            "salary": sal,
                            "tags": tags,
                            "url": href,
                        })
                        if len(results) >= limit:
                            break
    finally:
        proc.terminate()

    print(json.dumps({"count": len(results), "jobs": results}, ensure_ascii=False, indent=2))
    return results


async def cmd_apply(cfg, batch_limit=None):
    limit = batch_limit or cfg.get("target", {}).get("batch_apply_limit", 5)
    candidates = await cmd_search(cfg, limit=limit * 2)
    if not candidates:
        print(json.dumps({"applied": [], "message": "No new matching candidates found."}, ensure_ascii=False))
        return

    history = load_history()
    applied_comps = set(history.get("applied_companies", []))
    applied_urls = set(history.get("applied_urls", []))

    f_cfg = cfg.get("filters", {})
    bad_jd_kw = f_cfg.get("blocked_industry_keywords", []) + f_cfg.get("blocked_address_keywords", [])

    proc, port = launch_browser(cfg, "about:blank")
    await asyncio.sleep(2.0)
    contacted = []
    try:
        ws_url = await get_cdp_ws_url(port, "")
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url) as ws:
                msg_id = 1
                for job in candidates:
                    if len(contacted) >= limit:
                        break
                    comp = job["company"]
                    href = job["url"]
                    await cdp_eval(ws, f'location.href = "{href}"', msg_id); msg_id += 1
                    await asyncio.sleep(3.8)

                    page_desc = await cdp_eval(ws, '''document.querySelector('.job-sec-text, .desc')?.innerText || ""''', msg_id); msg_id += 1
                    page_addr = await cdp_eval(ws, '''document.querySelector('.location-address, .job-address, .job-location')?.innerText || ""''', msg_id); msg_id += 1

                    if any(b in (page_desc or "") or b in (page_addr or "") for b in bad_jd_kw):
                        applied_comps.add(comp)
                        applied_urls.add(href)
                        continue

                    btn_res = await cdp_eval(ws, '''(() => {
                        const btn = document.querySelector('a.btn-startchat, .btn-container a.btn-startchat, .op-btn-chat');
                        if (!btn) return {found: false};
                        const text = btn.innerText.trim();
                        btn.click();
                        return {found: true, text};
                    })()''', msg_id); msg_id += 1
                    await asyncio.sleep(2.8)

                    job["desc"] = page_desc or ""
                    job["address"] = page_addr or ""
                    job["chat_btn"] = btn_res
                    contacted.append(job)
                    applied_comps.add(comp)
                    applied_urls.add(href)

                if contacted:
                    await cdp_eval(ws, 'location.href = "https://www.zhipin.com/web/geek/chat"', msg_id); msg_id += 1
                    await asyncio.sleep(3.8)
                    for job in contacted:
                        comp = job["company"]
                        greeting = build_greeting(cfg, job["title"], job.get("desc", ""))
                        clicked = await cdp_eval(ws, f'''(() => {{
                            const items = Array.from(document.querySelectorAll('.user-list li .friend-content'));
                            const el = items.find(x => x.innerText.includes({json.dumps(comp)}));
                            if (!el) return false;
                            el.click();
                            return true;
                        }})()''', msg_id); msg_id += 1
                        if not clicked:
                            continue
                        await asyncio.sleep(1.2)
                        focused = await cdp_eval(ws, '''(() => {
                            const conv = document.querySelector('.chat-conversation');
                            if (!conv) return false;
                            const ed = conv.querySelector('#chat-input, .chat-input, [contenteditable="true"]');
                            if (!ed) return false;
                            ed.innerText = '';
                            ed.focus();
                            return true;
                        })()''', msg_id); msg_id += 1
                        if focused:
                            await ws.send_str(json.dumps({
                                "id": msg_id,
                                "method": "Input.insertText",
                                "params": {"text": greeting}
                            }))
                            await ws.receive()
                            msg_id += 1
                            await asyncio.sleep(0.4)
                            sent = await cdp_eval(ws, '''(() => {
                                const conv = document.querySelector('.chat-conversation');
                                const btn = conv.querySelector('.btn-send, button.btn-send') ||
                                            Array.from(conv.querySelectorAll('button, span, div')).find(el => el.innerText.trim() === '发送');
                                if (btn) { btn.click(); return true; }
                                return false;
                            })()''', msg_id); msg_id += 1
                            job["greeting_sent"] = bool(sent)
                            await asyncio.sleep(1.2)

        history["applied_companies"] = sorted(list(applied_comps))
        history["applied_urls"] = sorted(list(applied_urls))
        save_history(history)
    finally:
        proc.terminate()

    print(json.dumps({"applied_count": len(contacted), "applied": contacted}, ensure_ascii=False, indent=2))


async def cmd_check_chat(cfg):
    f_cfg = cfg.get("filters", {})
    ignore_kw = f_cfg.get("blocked_company_keywords", []) + f_cfg.get("blocked_industry_keywords", [])
    cand = cfg.get("candidate", {})
    persona = cand.get("assistant_persona", "AI 助手")
    max_scan = cfg.get("patrol", {}).get("max_chat_scan", 16)

    our_snippets = SYSTEM_DECLINE_SNIPPETS + [
        persona,
        "期待您的回复",
        "请您查收",
        "随时联系",
        "觉得我和贵公司的岗位很匹配",
    ]

    proc, port = launch_browser(cfg, "https://www.zhipin.com/web/geek/chat")
    await asyncio.sleep(1.5)
    new_replies = []
    all_recent = []
    try:
        ws_url = await get_cdp_ws_url(port, "zhipin.com")
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url) as ws:
                msg_id = 1
                for _ in range(15):
                    ready = await cdp_eval(ws, '''Boolean(document.querySelector('.user-list li'))''', msg_id); msg_id += 1
                    if ready:
                        break
                    await asyncio.sleep(0.4)

                items = await cdp_eval(ws, f'''(() => {{
                    const els = Array.from(document.querySelectorAll('.user-list li'));
                    return els.slice(0, {int(max_scan)}).map((el, idx) => {{
                        const title = el.querySelector('.title-box, .name-box')?.innerText?.replace(/\\n+/g, ' ') || '';
                        const text = el.innerText.replace(/\\n+/g, ' | ');
                        const badge = el.querySelector('.notice-badge, .badge, .unread-num, .badge-count')?.innerText?.trim() || '';
                        return {{idx, title, text, badge}};
                    }});
                }})()''', msg_id); msg_id += 1

                for it in (items or []):
                    if any(kw in it["title"] for kw in ignore_kw):
                        continue
                    is_our_last = any(snip in it["text"] for snip in our_snippets)
                    is_new = (not is_our_last) or bool(it["badge"])
                    entry = {
                        "idx": it["idx"],
                        "title": it["title"],
                        "badge": it["badge"],
                        "is_new_reply": is_new,
                        "summary": it["text"][:140],
                    }
                    if is_new:
                        await cdp_eval(ws, f'''(() => {{
                            const els = Array.from(document.querySelectorAll('.user-list li .friend-content'));
                            if (els[{it['idx']}]) els[{it['idx']}].click();
                        }})()''', msg_id); msg_id += 1
                        await asyncio.sleep(1.2)
                        conv_text = await cdp_eval(ws, '''(() => {
                            const conv = document.querySelector('.chat-conversation');
                            return conv ? conv.innerText.slice(-1200) : '';
                        })()''', msg_id); msg_id += 1
                        entry["conversation_tail"] = conv_text
                        new_replies.append(entry)
                    all_recent.append(entry)
    finally:
        proc.terminate()

    output = {
        "timestamp": datetime.datetime.now().isoformat(),
        "new_reply_count": len(new_replies),
        "new_replies": new_replies,
        "recent_chats": all_recent,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return output


async def cmd_reply(cfg, company_keyword, text=None, resume_keyword=None):
    proc, port = launch_browser(cfg, "https://www.zhipin.com/web/geek/chat")
    await asyncio.sleep(1.5)
    result = {"company_keyword": company_keyword, "resume_sent": False, "text_sent": False}
    try:
        ws_url = await get_cdp_ws_url(port, "zhipin.com")
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url) as ws:
                msg_id = 1
                for _ in range(15):
                    ready = await cdp_eval(ws, '''Boolean(document.querySelector('.user-list li .friend-content'))''', msg_id); msg_id += 1
                    if ready:
                        break
                    await asyncio.sleep(0.4)

                clicked = await cdp_eval(ws, f'''(() => {{
                    const items = Array.from(document.querySelectorAll('.user-list li .friend-content'));
                    const el = items.find(x => x.innerText.includes({json.dumps(company_keyword)}));
                    if (!el) return false;
                    el.click();
                    return true;
                }})()''', msg_id); msg_id += 1
                if not clicked:
                    result["error"] = f"Contact matching '{company_keyword}' not found in chat list."
                    print(json.dumps(result, ensure_ascii=False, indent=2))
                    return
                await asyncio.sleep(1.5)

                if resume_keyword:
                    rect_info = await cdp_eval(ws, '''(() => {
                        const conv = document.querySelector('.chat-conversation');
                        const btns = Array.from(conv.querySelectorAll('div, span, a'));
                        const el = btns.find(b => b.innerText && b.innerText.trim() === '发简历' && b.children.length <= 2);
                        if (!el) return null;
                        const r = el.getBoundingClientRect();
                        return {x: r.left + r.width / 2, y: r.top + r.height / 2};
                    })()''', msg_id); msg_id += 1

                    if rect_info:
                        msg_id = await cdp_click_xy(ws, rect_info["x"], rect_info["y"], msg_id)
                        await asyncio.sleep(1.2)
                        dlg_coords = await cdp_eval(ws, f'''(() => {{
                            const dlg = Array.from(document.querySelectorAll('body > div')).find(d => d.innerText && d.innerText.includes('请选择要发送的简历'));
                            if (!dlg) return null;
                            const items = Array.from(dlg.querySelectorAll('li, .resume-item, div'));
                            const targetItem = items.find(el => el.innerText && el.innerText.includes({json.dumps(resume_keyword)}));
                            const sendBtn = Array.from(dlg.querySelectorAll('button, span, a, div')).find(b => b.innerText && b.innerText.trim() === '发送' && b.children.length === 0);
                            const r1 = targetItem ? targetItem.getBoundingClientRect() : null;
                            const r2 = sendBtn ? sendBtn.getBoundingClientRect() : null;
                            return {{
                                itemXY: r1 ? {{x: r1.left + r1.width / 3, y: r1.top + r1.height / 2}} : null,
                                sendXY: r2 ? {{x: r2.left + r2.width / 2, y: r2.top + r2.height / 2}} : null
                            }};
                        }})()''', msg_id); msg_id += 1
                        if dlg_coords and dlg_coords.get("itemXY"):
                            msg_id = await cdp_click_xy(ws, dlg_coords["itemXY"]["x"], dlg_coords["itemXY"]["y"], msg_id)
                            await asyncio.sleep(0.6)
                        if dlg_coords and dlg_coords.get("sendXY"):
                            msg_id = await cdp_click_xy(ws, dlg_coords["sendXY"]["x"], dlg_coords["sendXY"]["y"], msg_id)
                            result["resume_sent"] = True
                            await asyncio.sleep(2.0)

                if text:
                    focused = await cdp_eval(ws, '''(() => {
                        const conv = document.querySelector('.chat-conversation');
                        if (!conv) return false;
                        const ed = conv.querySelector('#chat-input, .chat-input, [contenteditable="true"]');
                        if (!ed) return false;
                        ed.innerText = '';
                        ed.focus();
                        return true;
                    })()''', msg_id); msg_id += 1
                    if focused:
                        await ws.send_str(json.dumps({
                            "id": msg_id,
                            "method": "Input.insertText",
                            "params": {"text": text}
                        }))
                        await ws.receive()
                        msg_id += 1
                        await asyncio.sleep(0.4)
                        sent = await cdp_eval(ws, '''(() => {
                            const conv = document.querySelector('.chat-conversation');
                            const btn = conv.querySelector('.btn-send, button.btn-send') ||
                                        Array.from(conv.querySelectorAll('button, span, div')).find(el => el.innerText.trim() === '发送');
                            if (btn) { btn.click(); return true; }
                            return false;
                        })()''', msg_id); msg_id += 1
                        result["text_sent"] = bool(sent)
                        await asyncio.sleep(1.5)

                result["conversation_tail"] = await cdp_eval(ws, '''(() => {
                    const conv = document.querySelector('.chat-conversation');
                    return conv ? conv.innerText.slice(-600) : '';
                })()''', msg_id); msg_id += 1
    finally:
        proc.terminate()

    print(json.dumps(result, ensure_ascii=False, indent=2))


async def cmd_upload_resume(cfg, file_path):
    abs_path = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.exists(abs_path):
        raise FileNotFoundError(f"Resume file not found: {abs_path}")

    proc, port = launch_browser(cfg, "https://www.zhipin.com/web/geek/resume")
    await asyncio.sleep(2.5)
    try:
        ws_url = await get_cdp_ws_url(port, "zhipin.com")
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url) as ws:
                msg_id = 1
                await asyncio.sleep(2.5)
                await ws.send_str(json.dumps({"id": msg_id, "method": "DOM.getDocument", "params": {"depth": -1}}))
                while True:
                    msg = await ws.receive()
                    data = json.loads(msg.data)
                    if data.get("id") == msg_id:
                        root_id = data["result"]["root"]["nodeId"]
                        break
                msg_id += 1

                await ws.send_str(json.dumps({
                    "id": msg_id,
                    "method": "DOM.querySelector",
                    "params": {"nodeId": root_id, "selector": 'input[type="file"]'}
                }))
                while True:
                    msg = await ws.receive()
                    data = json.loads(msg.data)
                    if data.get("id") == msg_id:
                        input_node_id = data["result"]["nodeId"]
                        break
                msg_id += 1

                await ws.send_str(json.dumps({
                    "id": msg_id,
                    "method": "DOM.setFileInputFiles",
                    "params": {"nodeId": input_node_id, "files": [abs_path]}
                }))
                await ws.receive()
                msg_id += 1
                await asyncio.sleep(4.0)

                confirmed = await cdp_eval(ws, '''(() => {
                    const btns = Array.from(document.querySelectorAll('.upload-preview-dialog .btn, .dialog-wrap.active .btn, button, span, a'));
                    const confirmBtn = btns.find(b => b.innerText && b.innerText.trim() === '确定添加');
                    if (confirmBtn) { confirmBtn.click(); return true; }
                    return false;
                })()''', msg_id); msg_id += 1
                await asyncio.sleep(3.0)
                print(json.dumps({"uploaded": abs_path, "confirmed": bool(confirmed)}, ensure_ascii=False, indent=2))
    finally:
        proc.terminate()


async def cmd_shield(cfg, companies):
    proc, port = launch_browser(cfg, "https://www.zhipin.com/web/geek/privacy-set")
    await asyncio.sleep(2.5)
    shielded = []
    try:
        ws_url = await get_cdp_ws_url(port, "zhipin.com")
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url) as ws:
                msg_id = 1
                await asyncio.sleep(2.0)
                for kw in companies:
                    await cdp_eval(ws, '''(() => {
                        const btns = Array.from(document.querySelectorAll('button, a, span, div'));
                        const addBtn = btns.find(b => b.innerText && b.innerText.trim() === '添加屏蔽公司' && b.children.length === 0);
                        if (addBtn) addBtn.click();
                    })()''', msg_id); msg_id += 1
                    await asyncio.sleep(1.0)

                    await cdp_eval(ws, '''(() => {
                        const dlg = document.querySelector('.add-shield-company-dialog');
                        const inp = dlg ? dlg.querySelector('input') : null;
                        if (inp) { inp.value = ''; inp.focus(); }
                    })()''', msg_id); msg_id += 1
                    await ws.send_str(json.dumps({"id": msg_id, "method": "Input.insertText", "params": {"text": kw}}))
                    await ws.receive()
                    msg_id += 1
                    await asyncio.sleep(2.2)

                    btn_xy = await cdp_eval(ws, '''(() => {
                        const dlg = document.querySelector('.add-shield-company-dialog');
                        if (!dlg) return null;
                        const btn = Array.from(dlg.querySelectorAll('.btn, button, span, div')).find(b => b.innerText && b.innerText.includes('屏蔽所选公司') && b.children.length <= 1);
                        if (!btn) return null;
                        const r = btn.getBoundingClientRect();
                        return {x: r.left + r.width / 2, y: r.top + r.height / 2};
                    })()''', msg_id); msg_id += 1
                    if btn_xy:
                        msg_id = await cdp_click_xy(ws, btn_xy["x"], btn_xy["y"], msg_id)
                        shielded.append(kw)
                        await asyncio.sleep(2.0)
    finally:
        proc.terminate()

    print(json.dumps({"shielded": shielded}, ensure_ascii=False, indent=2))


async def cmd_watch(cfg, codex_hook=False):
    patrol = cfg.get("patrol", {})
    start_h, end_h = patrol.get("active_hours", [8, 23])
    interval = int(patrol.get("interval_seconds", 1800))
    print(f"[watch] Starting daytime patrol loop ({start_h:02d}:00-{end_h:02d}:00, every {interval}s)...")
    while True:
        now = datetime.datetime.now()
        if start_h <= now.hour < end_h:
            res = await cmd_check_chat(cfg)
            if res.get("new_reply_count", 0) > 0 and codex_hook:
                prompt = (
                    "New recruiter reply detected on BOSS Zhipin: "
                    + json.dumps(res["new_replies"], ensure_ascii=False)
                    + "\nPlease follow SKILL.md to craft a natural, strictly resume-grounded reply via boss_cli.py reply."
                )
                subprocess.run(["codex", "exec", prompt], check=False)
        await asyncio.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="BOSS Zhipin Raw-CDP Agent CLI for Codex / Claude Code / Antigravity")
    parser.add_argument("--config", help="Path to config.json", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("login", help="Open isolated browser profile to scan QR code and persist login session")

    p_search = sub.add_parser("search", help="Search and filter matching jobs with font de-obfuscation")
    p_search.add_argument("--query", default=None, help="Search keyword override")
    p_search.add_argument("--limit", type=int, default=10, help="Max jobs to return")

    p_apply = sub.add_parser("apply", help="Batch apply and send tailored AI assistant greetings")
    p_apply.add_argument("--limit", type=int, default=None, help="Number of companies to apply in this batch")

    sub.add_parser("check-chat", help="Fast (~6s) probe for new recruiter replies in chat list")

    p_reply = sub.add_parser("reply", help="Send a contextual reply and/or attachment resume to a recruiter")
    p_reply.add_argument("--company", required=True, help="Company or recruiter keyword to match in chat list")
    p_reply.add_argument("--text", default=None, help="Message text to send")
    p_reply.add_argument("--send-resume", default=None, help="Substring of uploaded PDF resume name to send")

    p_upload = sub.add_parser("upload-resume", help="Upload a local PDF resume to BOSS Zhipin attachment list")
    p_upload.add_argument("--file", required=True, help="Path to PDF resume file")

    p_shield = sub.add_parser("shield", help="Add companies to BOSS Zhipin blocked list")
    p_shield.add_argument("--add", nargs="+", required=True, help="Company keywords to block")

    p_watch = sub.add_parser("watch", help="Run periodic daytime chat patrol loop")
    p_watch.add_argument("--codex-hook", action="store_true", help="Invoke `codex exec` automatically when new replies arrive")

    args = parser.parse_args()
    cfg = load_config(args.config)

    if args.command == "login":
        asyncio.run(cmd_login(cfg))
    elif args.command == "search":
        asyncio.run(cmd_search(cfg, args.query, args.limit))
    elif args.command == "apply":
        asyncio.run(cmd_apply(cfg, args.limit))
    elif args.command == "check-chat":
        asyncio.run(cmd_check_chat(cfg))
    elif args.command == "reply":
        asyncio.run(cmd_reply(cfg, args.company, args.text, args.send_resume))
    elif args.command == "upload-resume":
        asyncio.run(cmd_upload_resume(cfg, args.file))
    elif args.command == "shield":
        asyncio.run(cmd_shield(cfg, args.add))
    elif args.command == "watch":
        asyncio.run(cmd_watch(cfg, args.codex_hook))


if __name__ == "__main__":
    main()
