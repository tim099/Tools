#!/usr/bin/env python3
# 區塊職責：tavern「叮 catchup」工具 — 確保 agent 被叮 / 醒來時不漏看同事訊息。
# 物理意義：
#   叮機制原本只要求 agent 回 ack，但 chat 端不會自動推 tavern 新訊息給 agent；
#   結果 agent 經常漏看別人在叮之前發的 task-share / @同事們。
#   本工具掃最近 N 筆（預設 10）tavern 訊息，比對 per-persona cursor，只印未看過的，
#   並推進 cursor。ucl-ding skill Step 0 強制先跑本工具再決定 ack 內容。
# 數值影響：
#   只讀（掃 messages JSON 檔），不修改 asset / token；唯一寫檔是
#   AgentCommands/ChatTavern/_inbox_cursor/<persona>.json 推進 last_seen_ts。
#
# 用法：
#   python AgentCommands/Tools/tavern_catchup.py [--persona <p>] [--min 10]
#                                                [--include-self] [--quiet-system] [--reset]
#
# 設計理由 (Tim 2026-05-28 派 task)：
#   舊叮機制盲點 — agent 收到叮直接 ack，沒讀 tavern → 漏看 basecamp 的 task-share。
#   Tim 拍板：「確保會讀到至少最近 10 筆消息(無論是否提及自己)，已看過的可排除」。
#   per-persona cursor 解 multi-persona 共享 chat 環境的隔離問題（gura 已看 ≠ ridge-001 已看）。

import argparse
import json
import os
import sys
import glob
from datetime import datetime, timezone

# 區塊職責：Windows 端編碼安全保護
# 物理意義：強制 stdout/stderr 輸出編碼為 utf-8，避免 cp950/cp437 終端列印 Emoji 崩潰
# 數值影響：無修改，僅改善終端顯示相容性
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except AttributeError:
    pass

# ===========================================================
# 路徑解析
# ===========================================================

# 區塊職責：找 repo root — 錨定「其下有 AgentCommands/ChatTavern 的那層」
# 物理意義：本工具要讀的訊息根固定在 <root>/AgentCommands/ChatTavern/，直接認這個子路徑最可靠。
# 數值影響：**不再靠 .git 偵測**。AgentCommands 已是 git submodule，其 .git 為 gitlink『檔』，
#          舊版「遇 .git 檔/目錄就停」會誤停在 submodule 根 AgentCommands，使 REPO_ROOT 少算一層、
#          MESSAGES_DIR 多疊一個 AgentCommands → isdir=False → 永遠撈空 + cursor 不推進
#          (2026-06-16 critical bug：叮 catchup 靜默失效、agent 誤報「都看過了」)。
def find_repo_root() -> str:
    # anchor：某層其下要看得到 AgentCommands/ChatTavern（本工具真正需要的訊息根）
    def _has_anchor(d: str) -> bool:
        return bool(d) and os.path.isdir(os.path.join(d, "AgentCommands", "ChatTavern"))
    # 優先吃 CLAUDE_PROJECT_DIR env（Claude Code 注入），但仍須通過 anchor 驗證才採用
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root and _has_anchor(env_root):
        return env_root
    here = os.path.abspath(os.path.dirname(__file__))
    cur = here
    while cur and cur != os.path.dirname(cur):
        if _has_anchor(cur):
            return cur
        cur = os.path.dirname(cur)
    # 最後保底：本檔在 AgentCommands/Tools/，外層 repo 根即兩層上
    return os.path.abspath(os.path.join(here, "..", ".."))

REPO_ROOT = find_repo_root()
MESSAGES_DIR = os.path.join(REPO_ROOT, "AgentCommands", "ChatTavern", "rooms", "tavern", "messages")
CURSOR_DIR = os.path.join(REPO_ROOT, "AgentCommands", "ChatTavern", "_inbox_cursor")
SESSION_DIR = os.path.join(REPO_ROOT, "AgentCommands", "_session")
# R2 讀取端收斂 (2026-07-24)：durable inbox 目錄 + persona pool（persona→agent 反查用）
INBOX_DIR = os.path.join(REPO_ROOT, "AgentCommands", "ChatTavern", "rooms", "tavern", "inbox")
PERSONAS_DIR = os.path.join(REPO_ROOT, "AgentCommands", "AwakenInit", "personas")
# T-PATH-02: UCL_Core Tools~/AgentCommands 走 layout-agnostic resolver, 不再寫死 CardGame/...
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)
from AgentCommands._lib import tavern_paths as _tp  # noqa: E402
AWAKENING_DIR = str(_tp.UCL_AGENTCMD_DIR)


# ===========================================================
# Persona 解析 — 自動反查 session lock
# ===========================================================

# 區塊職責：自動推斷當前 caller 對應的 persona
# 物理意義：reuse awakening.py 的 compute_claim_origin → 找 _session/_persona_*.json 中
#          claim_origin 相符且未過期、locked_at 最新的那筆。失敗回 None 由 caller 處理。
# 數值影響：不寫檔；純讀。
def resolve_persona_auto(strict: bool = True):
    """
    回 (persona_or_None, live_locks_list)。
    多 live lock 同 claim_origin 時:
      - strict=True (預設, T33.2 後): 回 (None, live) — caller 應印警告強制 --persona
      - strict=False: 回 (max(locked_at).persona, live) — 舊行為, 容易取錯
    T33.2 血證 (2026-06-09): summit 跑 catchup 被 auto-resolve 誤判為 basecamp,
    取錯 cursor 過濾掉所有 summit 視角未讀訊息 → ucl-ding spec 違規。
    """
    try:
        if AWAKENING_DIR not in sys.path:
            sys.path.insert(0, AWAKENING_DIR)
        import importlib
        awk = importlib.import_module("awakening")

        my_origin = awk.compute_claim_origin()
        live = []
        for lp in glob.glob(os.path.join(SESSION_DIR, "_persona_*.json")):
            try:
                with open(lp, "r", encoding="utf-8") as f:
                    lock = json.load(f)
            except Exception:
                continue
            try:
                if awk.is_lock_expired(lock):
                    continue
                if awk.lock_claim_origin(lock) != my_origin:
                    continue
                live.append(lock)
            except Exception:
                continue
        if not live:
            return (None, [])
        if len(live) > 1 and strict:
            # T33.2: 多 live lock 同 origin → 不猜, 讓 caller 決定
            return (None, live)
        latest = max(live, key=lambda d: d.get("locked_at", ""))
        return ((latest.get("persona") or "").strip() or None, live)
    except Exception as e:
        print(f"⚠ persona 自動反查失敗（{type(e).__name__}: {e}）— 請顯式 --persona", file=sys.stderr)
        return (None, [])


# ===========================================================
# Cursor I/O
# ===========================================================

# 區塊職責：載 / 存 per-persona cursor
# 物理意義：cursor 是 ISO 字串時戳 (last_seen_ts)；ts > cursor 即未看
# 數值影響：寫入 _inbox_cursor/<persona>.json，原子取代（先寫 tmp 再 rename）
def cursor_path(persona: str) -> str:
    return os.path.join(CURSOR_DIR, f"{persona}.json")

def load_cursor(persona: str) -> str:
    """回傳 last_seen_ts 字串；無 cursor 回 None。"""
    p = cursor_path(persona)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return (json.load(f) or {}).get("last_seen_ts")
    except Exception:
        return None

def save_cursor(persona: str, last_seen_ts: str):
    os.makedirs(CURSOR_DIR, exist_ok=True)
    p = cursor_path(persona)
    tmp = p + ".tmp"
    payload = {
        "last_seen_ts": last_seen_ts,
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)


# ===========================================================
# 訊息讀取
# ===========================================================

# 區塊職責：抓最新 N 筆 tavern 訊息
# 物理意義：messages/YYYY-MM-DD/HHMMSS_MMM_UUID.json，檔名同日內可字典序，跨日靠目錄名
# 數值影響：純讀；只回傳 dict list，沒副作用
def fetch_recent_messages(min_count: int, scan_days: int = 7) -> list:
    """掃最近 scan_days 個日期目錄，按 ts 升序回傳最後 min_count 筆。"""
    if not os.path.isdir(MESSAGES_DIR):
        return []
    # 取最近 scan_days 個 date 目錄（reverse-sorted by name）
    date_dirs = sorted([d for d in os.listdir(MESSAGES_DIR)
                        if os.path.isdir(os.path.join(MESSAGES_DIR, d))], reverse=True)
    files = []
    for dd in date_dirs[:scan_days]:
        day_dir = os.path.join(MESSAGES_DIR, dd)
        for fname in sorted(os.listdir(day_dir), reverse=True):
            if fname.endswith(".json"):
                files.append(os.path.join(day_dir, fname))
                if len(files) >= min_count * 3:  # 多抓些 buffer 避免 filter 後不足
                    break
        if len(files) >= min_count * 3:
            break

    msgs = []
    for fp in files:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                d = json.load(f)
            msgs.append(d)
        except Exception:
            continue

    # 按 ts 升序排，取最後 min_count 筆（最新 window）
    msgs.sort(key=lambda m: m.get("ts", ""))
    return msgs[-min_count:] if len(msgs) > min_count else msgs


# ===========================================================
# 過濾 + 顯示
# ===========================================================

# 區塊職責：判斷一筆訊息是否屬「系統噪音」(酒保時段提醒 / bartender relay)
# 物理意義：--quiet-system 要濾的是「噪音」而非「所有酒保訊息」。原本 blanket 濾 sender_id==tavern-keeper
#          會連銀行帳務通知(打款/轉帳/開戶/發券, 也走 tavern-keeper 身分廣播)一起殺掉 —— 導致 @ 到某
#          persona 的發券通知, 他自己 --quiet-system catchup 反而收不到 (Tim 2026-07-24 抓包)。
#          帳務通知早已帶可區分的 tag(bank-* / voucher-*), 據此白名單放行, 其餘酒保訊息(時段提醒等)照舊濾。
# 白名單: tavern-keeper 發的、tag 為 bank-* / voucher-* = 重要帳務事件, 非噪音, 不濾。
_IMPORTANT_KEEPER_TAG_PREFIXES = ("bank-", "voucher-")


def is_system_msg(msg: dict) -> bool:
    tag = (msg.get("meta") or {}).get("tag") or ""
    if tag.startswith("bartender-relay"):
        return True
    if msg.get("sender_id") == "tavern-keeper":
        # 帳務通知(bank-*/voucher-*)雖由酒保身分廣播, 屬重要事件 → 放行(不當系統噪音)
        if any(tag.startswith(p) for p in _IMPORTANT_KEEPER_TAG_PREFIXES):
            return False
        return True
    return False

# 區塊職責：壓縮 body 成單行 + 截斷，方便快速掃讀
def compact_body(body: str, max_chars: int = 240) -> str:
    if not body:
        return ""
    # 折行 / 多空白合併
    s = " ".join(body.replace("\r", "\n").replace("\n", " ⏎ ").split())
    if len(s) > max_chars:
        s = s[:max_chars] + "…"
    return s

# 區塊職責：印單筆訊息
def print_msg(msg: dict):
    ts = msg.get("ts", "")
    # 取 HH:MM:SS（UTC→台北只需 +8）
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        # 轉台北顯示
        from datetime import timedelta
        local = dt + timedelta(hours=8)
        time_str = local.strftime("%H:%M:%S")
    except Exception:
        time_str = ts[11:19] if len(ts) >= 19 else ts
    sender = msg.get("sender_name") or msg.get("sender_id") or "?"
    persona = msg.get("sender_persona") or ""
    tag = (msg.get("meta") or {}).get("tag") or ""
    head = f"[{time_str}] {sender}" + (f"@{persona}" if persona else "")
    if tag:
        head += f"  «{tag}»"
    print(head)
    print(f"   {compact_body(msg.get('body', ''))}")


# ===========================================================
# Inbox surface (R2 讀取端收斂, 2026-07-24) — persona-keyed durable inbox
# ===========================================================
# 區塊職責：把 persona 的 durable inbox（@提及 / task handoff 落檔）在叮時一併 surface，
#          跟上方訊息掃描合成單一「我該處理什麼」視圖（解「@→inbox 寫」跟「叮→掃訊息」兩套分裂）。
# 物理意義：R2 persona-first 後 @persona 寫 inbox/<persona>.md、@agent 寫 inbox/<agent>.md；
#          catchup 讀「自己 persona.md ＋ 所屬 agent.md」兩層（basecamp 2026-07-24 拍磚：agent 是共用信箱層）。
# 收斂原則（不造第三追蹤器）：inbox 已讀狀態 = 檔內有無內容（inbox_ack.py archive 後清空）；
#          本段純唯讀 surface，不推進任何 cursor、不動 inbox — 清除仍歸 inbox_ack.py（單一 mutator）。
def resolve_owning_agent(persona: str) -> str:
    """讀 AwakenInit/personas/<persona>.json 的 agent 欄；缺檔/失敗回空字串。"""
    pf = os.path.join(PERSONAS_DIR, f"{persona}.json")
    try:
        with open(pf, "r", encoding="utf-8") as f:
            return (json.load(f).get("agent") or "").strip()
    except Exception:
        return ""


def read_inbox_entries(inbox_id: str):
    """回 inbox/<inbox_id>.md 內每筆條目的 title 清單；檔缺/空/讀失敗回 []。

    條目分隔錨定 '## [seq=' — 跟 inbox_ack.py count_mentions 同一約定（AppendInbox 寫 '## [seq=N] <title>'）。
    只認這個 prefix 可避開被引用訊息 body 內的 markdown '## 標題' 汙染 title 清單。
    """
    p = os.path.join(INBOX_DIR, f"{inbox_id}.md")
    if not os.path.isfile(p):
        return []
    try:
        with open(p, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return []
    titles = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("## [seq="):
            titles.append(s[3:].strip())
    return titles


def surface_inbox(persona: str):
    """印 persona.md（persona 層）＋ owning agent.md（agent 共用層）的未讀 inbox 條目（唯讀）。"""
    # persona 層永遠看；agent 層只在能反查到、且 ≠ persona 名時加（避免重複讀同一檔）
    layers = [("persona", persona)]
    agent = resolve_owning_agent(persona)
    if agent and agent != persona:
        layers.append(("agent", agent))
    any_shown = False
    for layer_name, inbox_id in layers:
        titles = read_inbox_entries(inbox_id)
        if not titles:
            continue
        any_shown = True
        print(f"📥 inbox/{inbox_id}.md（{layer_name} 層 · {len(titles)} 筆待處理）")
        for t in titles[:10]:
            print(f"   • {t}")
        if len(titles) > 10:
            print(f"   …還有 {len(titles) - 10} 筆（打「已讀」歸檔後不再重複列）")
        print()
    if any_shown:
        print("   ↳ 處理完跑 inbox_ack.py 歸檔（persona 層 --agent <persona> / agent 層 --agent <agent>），下次叮就只剩真新。")
        print()


# ===========================================================
# 主流程
# ===========================================================

def main():
    ap = argparse.ArgumentParser(
        description="叮 catchup：印出最近 N 筆 tavern 中尚未看過的訊息，並推進 per-persona cursor。",
    )
    ap.add_argument("--persona", default=None,
                    help="目標 persona（缺則自動反查 session lock）。")
    ap.add_argument("--min", type=int, default=10, dest="min_count",
                    help="檢視 window 大小（最近 N 筆）；預設 10。")
    ap.add_argument("--include-self", action="store_true",
                    help="預設過濾自己發的訊息；加此 flag 顯示。")
    ap.add_argument("--quiet-system", action="store_true",
                    help="隱藏酒保系統廣播（時段提醒、結算等）。")
    ap.add_argument("--reset", action="store_true",
                    help="重置 cursor（刪除 cursor 檔，下次叮會看到全部 window）。")
    args = ap.parse_args()

    # 區塊：persona 解析 (T33.2 — multi-lock 同 origin 警告)
    # 物理意義：caller 帶 --persona 直接吃；否則 auto-resolve, 但多 live lock 時拒猜
    # 數值影響：擋掉「summit 跑 catchup 卻被當 basecamp」這類 cursor 取錯場景
    if args.persona:
        persona = args.persona.strip()
    else:
        auto_persona, live_locks = resolve_persona_auto(strict=True)
        if len(live_locks) > 1:
            names = [(l.get("persona") or "?") for l in live_locks]
            print(
                "⚠ T33.2: 偵測到同 claim_origin 下多個 live lock — "
                f"{', '.join(names)}\n"
                "  auto-resolve 不安全（會取『最後鎖』, 可能 ≠ 當前 caller）\n"
                "  請顯式 --persona <self>（看自己的 morning lock 檔案名）",
                file=sys.stderr,
            )
            return 2
        persona = (auto_persona or "").strip()
    if not persona:
        print("❌ 無法解析 persona（自動反查失敗且未顯式 --persona）", file=sys.stderr)
        return 2

    # 區塊：--reset 處理
    if args.reset:
        p = cursor_path(persona)
        if os.path.isfile(p):
            os.remove(p)
            print(f"✓ cursor 已重置：{persona}（檔案已刪除）")
        else:
            print(f"ℹ {persona} 本來就沒 cursor，無需重置")
        return 0

    # 區塊：抓 window
    window = fetch_recent_messages(args.min_count)
    if not window:
        print("ℹ tavern 沒有訊息可讀。")
        return 0

    # 區塊：載 cursor + 過濾
    last_seen = load_cursor(persona)
    unseen = []
    for m in window:
        ts = m.get("ts", "")
        if last_seen and ts <= last_seen:
            continue
        if not args.include_self and m.get("sender_persona") == persona:
            continue  # 自己的 post 不重複給自己看
        if args.quiet_system and is_system_msg(m):
            continue
        unseen.append(m)

    # 區塊：輸出
    print(f"📬 叮 catchup（persona={persona}, 檢視最近 {len(window)} 筆，cursor={last_seen or '(無)'}）")
    print()
    if not unseen:
        print(f"✓ 沒有未看過的新訊息。")
    else:
        print(f"== {len(unseen)} 筆未看訊息 ==")
        for m in unseen:
            print_msg(m)
            print()

    # 區塊：surface durable inbox（R2 讀取端收斂）— 訊息掃描是「最近活動窗」(ephemeral)，
    #        inbox 是「未 ack 前一直在」的 durable 待辦層；兩者合成單一「我該處理什麼」視圖。
    surface_inbox(persona)

    # 區塊：推進 cursor（推到 window 最大 ts，無論是否實際顯示 — agent 已被告知有 window）
    new_cursor = max(m.get("ts", "") for m in window if m.get("ts"))
    if new_cursor and (not last_seen or new_cursor > last_seen):
        save_cursor(persona, new_cursor)
        print(f"✓ cursor 推進到 {new_cursor}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
