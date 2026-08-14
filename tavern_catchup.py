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

# 區塊職責：目前有 live lock 的 persona 一覽（給叮 / catchup 的表頭用）。
# 物理意義：被叮的人第一件事是「進 context」，而「現在誰在線」是 context 的一部分 ——
#          之前要另外跑一支指令才看得到，於是實務上沒人看。整合進既有輸出＝不增加步驟。
# 數值影響：過期判定沿用 awakening.is_lock_expired（同一套規則，不另立標準）；
#          讀不到一律回空 list —— **空不代表沒人在線，只代表查不到**，所以顯示時要講清楚。
def list_online_personas():
    try:
        if AWAKENING_DIR not in sys.path:
            sys.path.insert(0, AWAKENING_DIR)
        import importlib
        awk = importlib.import_module("awakening")
        out = []
        for lp in glob.glob(os.path.join(SESSION_DIR, "_persona_*.json")):
            try:
                with open(lp, "r", encoding="utf-8") as f:
                    lock = json.load(f)
                if awk.is_lock_expired(lock):
                    continue
                name = (lock.get("persona") or "").strip()
                if name:
                    out.append(name)
            except Exception:
                continue
        return sorted(out)
    except Exception:
        return []


def format_online_line(me: str = "") -> str:
    """一行字：🟢 在線：a, b, c（自己標星號）。查不到時明說『查不到』而不是印空。"""
    names = list_online_personas()
    if not names:
        return "🟢 在線：(查不到 lock — 不代表沒人在線)"
    shown = [f"{n}*" if n == me else n for n in names]
    return f"🟢 在線（{len(names)}）：{', '.join(shown)}" + ("　* = 你" if me in names else "")


# 區塊職責：在線明細（給 ding brief 用；比一行版多出「憑什麼說他在線」）。
# 物理意義：一行版只給名字，無法回答「這個名字是新鮮的還是過期 lock」——
#          而 @ 一個其實不在線的人是靜默失敗：訊息發出去、沒人回，看起來像對方不理你。
# 數值影響：純讀 _session/_persona_*.json；過期判定沿用 awakening.is_lock_expired（不另立標準）。
#          **列出全部 lock 檔（含過期），過期的標明** —— 只列 live 的會讓「有 lock 但過期」
#          跟「從來沒登入過」長得一模一樣，而這兩者要採取的行動不同。
def online_detail_rows() -> list:
    """回 [(persona, live?, bank)]，依 persona 排序。

    只給三欄 —— persona / 是否在線 / 該 persona 的 **bank 帳戶**。
    locked_at 與 session_key 刻意不列（Tim 2026-08-04）：叮要的是「誰在、歸誰的帳」，
    而「這筆 lock 新不新鮮」的結論已經由 🟢/⚪ 表達 —— 把證據攤出來只是讓人再判一次。
    第三欄取 lock 的 `bank_account`（Tim 2026-08-04 拍板 A 案），理由是 registry 的
    `agent` 欄跨 persona 裝著兩種東西 —— summit/ame 存身分名 `Zeta`、basecamp 存工具名
    `claude-code` —— 欄名叫 agent 而內容是混的，拿它當「帳戶」顯示會印出錯類別的值。
    `bank_account` 字面就是帳戶，跨 persona 一致。讀不到才退回 registry agent。
    """
    rows = []
    try:
        if AWAKENING_DIR not in sys.path:
            sys.path.insert(0, AWAKENING_DIR)
        import importlib
        awk = importlib.import_module("awakening")
        expired_of = awk.is_lock_expired
    except Exception:
        expired_of = None
    for lp in sorted(glob.glob(os.path.join(SESSION_DIR, "_persona_*.json"))):
        try:
            with open(lp, "r", encoding="utf-8") as f:
                lock = json.load(f)
        except Exception:
            rows.append((os.path.basename(lp), None, ""))
            continue
        name = (lock.get("persona") or os.path.basename(lp)).strip()
        live = None if expired_of is None else (not expired_of(lock))
        rows.append((name, live,
                     lock.get("bank_account") or resolve_owning_agent(name)
                     or lock.get("agent") or ""))
    return rows


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


# ===========================================================
# Ding brief — 每次叮留下「這次到底讀到了什麼」的可稽核副本
# ===========================================================
# 區塊職責：把本次 catchup 的**實際輸出**落檔成 letters/<persona>/_ding_brief.md（每次覆蓋）。
# 物理意義：叮的輸出原本只存在於 stdout —— 一旦 agent 沒跑工具而自己手撈訊息，
#          外人看不出差別（Tim 2026-08-04 抓包：summit 手寫 python 讀訊息，
#          於是完全繞過 format_online_line，@ 了一個其實不在線的 gura）。
#          **有檔＝跑過，沒檔或 ts 是舊的＝沒跑。** 這是可驗證性，不是紀錄癖。
# 數值影響：唯一新增寫檔；純附加產物，不影響 cursor / token / 任何既有狀態。
#          內容是 stdout 的 tee —— **不重建、不改寫**，所以檔案跟 agent 讀到的東西
#          不可能漂移（重建一份「應該一樣」的副本才會漂）。
def ding_brief_path(persona: str) -> str:
    return os.path.join(REPO_ROOT, "AgentCommands", "ChatTavern", "baton",
                        "letters", persona, "_ding_brief.md")


class _Tee:
    """同時寫真 stdout 與 buffer；不吞例外、不改內容。"""

    def __init__(self, real):
        self._real = real
        self.buf = []

    def write(self, s):
        self.buf.append(s)
        return self._real.write(s)

    def flush(self):
        self._real.flush()

    def __getattr__(self, name):          # encoding / isatty 等一律轉給真 stdout
        return getattr(self._real, name)


def write_ding_brief(persona: str, captured: str, argv_note: str) -> str:
    """落檔並回路徑；寫不了就回 ''（叮本身不該因為留存失敗而失敗）。"""
    p = ding_brief_path(persona)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    rows = online_detail_rows()
    L = [
        "---",
        "type: ding_brief",
        f"persona: {persona}",
        f"generated_at: {now}",
        "generated: mechanical   # 每次叮覆蓋 —— 手改無效，內容是 catchup stdout 的 tee",
        f"invocation: {argv_note}",
        "---",
        "",
        f"# 📬 Ding Brief — {persona}",
        "",
        "> 本檔＝**這次叮實際讀到的東西**（stdout 逐字 tee，非事後重建）。",
        "> `generated_at` 不是剛剛 → 這次叮沒跑工具，下面的內容是上一次的。",
        "",
        "## 🟢 在線明細（憑 `_session/_persona_*.json` 的 lock）",
        "",
        "| persona | 狀態 | Bank（帳戶） |",
        "|---|---|---|",
    ]
    if not rows:
        L.append("| (無 lock 檔) | — | — |")
    else:
        for name, live, agent in rows:
            mark = "🟢 在線" if live else ("⚪ lock 已過期" if live is False else "❔ 判不出")
            me = "　**← 你**" if name == persona else ""
            L.append(f"| `{name}`{me} | {mark} | {agent} |")
    L += [
        "",
        "> ⚠ **空或查不到 ≠ 沒人在線**，只代表查不到 lock。",
        "> 反過來也要小心：**沒列在這張表上的人，不要當成在線來 @** ——",
        "> @ 一個不在線的人是靜默失敗（訊息發出去、沒人回，看起來像對方不理你）。",
        "",
        "## 📄 本次 catchup 輸出（逐字）",
        "",
        "```text",
        captured.rstrip("\n") or "(無輸出)",
        "```",
    ]
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(L) + "\n")
        os.replace(tmp, p)
        return p
    except Exception as e:
        print(f"⚠ ding brief 落檔失敗（叮本身不受影響）：{e}", file=sys.stderr)
        return ""

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
def print_msg(msg: dict, full: bool = False):
    """印一筆訊息。full=True 不截斷內文（--full）。"""
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
    aBody = msg.get("body", "")
    print(f"   {aBody if full else compact_body(aBody)}")


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


INBOX_SNIPPET_CHARS = 110      # 摘要上限；夠判斷「這筆值不值得點進去」就好

# 區塊職責：筆數參數改吃 ChatTavern/render_settings.json（UCL_ChatTavernAdminPage 可調）。
# 物理意義：這些數字原本硬編在本檔，而 ucl-ding 的規則正文也各寫一份（「最近 5 條 / 近 20 條」）
#          —— 同一個數字兩處各存一份，天生會漂（而漂了不會有人喊）。
#          C# 端寫、Python 端讀，**單一事實源**；缺檔／缺欄一律落回這裡的預設。
# 數值影響：純讀；CLI 顯式帶值仍優先（設定只提供「預設」，不奪走現場覆蓋能力）。
SETTINGS_PATH = os.path.join(REPO_ROOT, "AgentCommands", "ChatTavern", "render_settings.json")
_DEFAULTS = {"ding_window_count": 10, "ding_context_count": 5, "ding_inbox_show_count": 10}


def setting(key: str) -> int:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            v = (json.load(f) or {}).get(key)
        if isinstance(v, int) and 1 <= v <= 500:
            return v
    except Exception:
        pass
    return _DEFAULTS[key]


# 區塊職責：從一筆 inbox 條目的行群裡撈出「第一句內文」。
# 物理意義：條目本體長這樣 —— 標題行 / `_at …_` / 「在房間 X，Y 提到了你：」/ `> <內文第一行>` / 內文續行。
#          原本只取標題（「💬 被 X 提及 (seq=N)」），那句話**不含任何內容**，
#          於是 47 筆待辦長得一模一樣、無法排序、實務上整批被跳過。
# 數值影響：純讀，不改檔。內文已經在 inbox 檔裡（引言區）—— 這是**讀取端把它丟掉**，
#          所以修讀取端就好，不必動寫入端、也不必為既有 47 筆做任何遷移。
def _entry_snippet(lines: list) -> str:
    picked = []
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("_at ") or s.startswith("在房間 ") or s.startswith("建議動作"):
            continue
        if s.startswith(">"):
            s = s.lstrip("> ").strip()
        if not s:
            continue
        picked.append(s)
        if sum(len(x) for x in picked) >= 20:     # 太短的第一行（純標題句）再補一行才有判斷價值
            break
    if not picked:
        return ""
    text = " ".join(picked).replace("**", "")
    return text[:INBOX_SNIPPET_CHARS] + ("…" if len(text) > INBOX_SNIPPET_CHARS else "")


def read_inbox_entries(inbox_id: str):
    """回 inbox/<inbox_id>.md 內每筆條目的 (title, snippet)；檔缺/空/讀失敗回 []。

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
    entries, cur_title, cur_lines = [], None, []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("## [seq="):
            if cur_title is not None:
                entries.append((cur_title, _entry_snippet(cur_lines)))
            cur_title, cur_lines = s[3:].strip(), []
        elif cur_title is not None:
            cur_lines.append(line)
    if cur_title is not None:
        entries.append((cur_title, _entry_snippet(cur_lines)))
    return entries


def surface_inbox(persona: str, inbox_show: int = None):
    """印 persona.md（persona 層）＋ owning agent.md（agent 共用層）的未讀 inbox 條目（唯讀）。"""
    if inbox_show is None:
        inbox_show = setting("ding_inbox_show_count")
    # **只看 persona 層。** agent 層 inbox 已退場（Tim 2026-08-04：agent 現在代表的是銀行帳戶，
    # 不是身分層）—— 一個帳戶名下所有 persona 共用一份待辦，等於誰都不負責，
    # 而它的 backlog（Zeta 層 48 筆）也從來沒有人清。@ 要送到人，不是送到戶頭。
    layers = [("persona", persona)]
    any_shown = False
    for layer_name, inbox_id in layers:
        entries = read_inbox_entries(inbox_id)
        if not entries:
            continue
        any_shown = True
        # **取最新 N 筆，不是最舊 N 筆。** 血證 2026-08-04：積了 47 筆待辦時，舊版印最舊 10 筆
        # （7/24 的），而當天 8 筆真正該回的 @ 全被折進「還有 37 筆」——
        # 叮問的是「剛剛發生什麼」，舊版答的是「最久以前欠什麼」，兩者在有 backlog 時完全不重疊。
        # 較舊的仍以筆數明示（禁靜默截斷），要逐筆看就直接讀 inbox 檔。
        recent = entries[-inbox_show:] if inbox_show > 0 else entries
        older = len(entries) - len(recent)
        head = f"📥 inbox/{inbox_id}.md（{layer_name} 層 · {len(entries)} 筆待處理"
        head += f"，以下為**最新 {len(recent)} 筆**）" if older else "）"
        print(head)
        for t, snip in recent:
            print(f"   • {t}")
            if snip:
                print(f"     ↳ {snip}")
        if older:
            print(f"   …另有 {older} 筆較舊（最舊的在 inbox 檔頂端；打「已讀」歸檔後不再重複列）")
        print()
    if any_shown:
        # inbox_ack.py 住在 UCL_Core 的 CommandResolver/，跟本檔不同層 —— 提示不印完整路徑
        # 的話 agent 會憑直覺猜 AgentCommands/Tools/（wake#48 實踩），所以這裡印解析後的實路徑。
        ack_path = os.path.join(AWAKENING_DIR, "CommandResolver", "inbox_ack.py")
        try:
            ack_disp = os.path.relpath(ack_path, REPO_ROOT).replace("\\", "/")
        except ValueError:
            ack_disp = ack_path.replace("\\", "/")
        print(f"   ↳ 處理完跑 python {ack_disp} 歸檔（persona 層 --agent <persona> / agent 層 --agent <agent>），下次叮就只剩真新。")
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
    ap.add_argument("--min", type=int, default=None, dest="min_count",
                    help="檢視 window 大小（最近 N 筆）；預設讀 render_settings.json "
                         "的 ding_window_count（管理頁可調，內建 10）。")
    ap.add_argument("--include-self", action="store_true",
                    help="預設過濾自己發的訊息；加此 flag 顯示。")
    ap.add_argument("--quiet-system", action="store_true",
                    help="隱藏酒保系統廣播。**叮不建議用**（Tim 2026-08-04：酒保訊息現在多半是"
                         "打款／獎金這類重要事件，不是噪音）。用了會印出被藏幾筆。")
    ap.add_argument("--context", type=int, default=None,
                    help="未看訊息少於 N 筆時，補印已看過的最近訊息湊到 N 筆掌握近況"
                         "（預設讀 ding_context_count，內建 5 = ucl-ding 的「至少讀最近 5 條」；0 = 不補）。")
    # 區塊職責：自帶輸出上限與詳略切換 —— **把「需要 | head」這個理由移走**
    # 物理意義：🩸 血證：Windows 上 `catchup | head` 會讓 head 提早關管線 → print 拋
    #          BrokenPipeError → main 死在中途 → **cursor 永遠不推進**，而 pipeline 的退出碼
    #          是 head 的 0。我當時看到「cursor 卡住」的第一個念頭是「工具有問題」，
    #          而工具是無辜的。真正該修的不是那條管線，是**讓人不必接那條管線**。
    # 數值影響：--limit 只影響「未看訊息」的印出筆數（不影響 window 掃描與 cursor 語意）；
    #          --full 關掉單筆截斷。兩者都純顯示層。
    ap.add_argument("--limit", type=int, default=None,
                    help="最多印幾筆未看訊息（其餘只報數量）。想少讀用這個，"
                         "**不要用 | head** —— 那會讓 cursor 靜默不推進。")
    ap.add_argument("--full", action="store_true",
                    help="不截斷單筆內文（預設截斷）。")
    ap.add_argument("--reset", action="store_true",
                    help="重置 cursor（刪除 cursor 檔，下次叮會看到全部 window）。")
    args = ap.parse_args()
    # None = 未顯式指定 → 落回管理頁設定（單一事實源）；顯式帶值一律優先
    if args.min_count is None:
        args.min_count = setting("ding_window_count")
    if args.context is None:
        args.context = setting("ding_context_count")

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

    # 區塊：ding brief tee（persona 已定案之後才裝 —— 之前的錯誤沒有歸屬對象可落檔）
    # 物理意義：把 _run 的 stdout 逐字複製一份到 letters/<persona>/_ding_brief.md。
    #          裝在這裡而不是每個 print 各自寫，是因為 print 散在 print_msg / surface_inbox 裡，
    #          逐點改一定漏（而漏掉的那幾行沒有任何檢查會發現）。
    real = sys.stdout
    tee = _Tee(real)
    sys.stdout = tee
    try:
        code = _run(args, persona)
    finally:
        sys.stdout = real
        bp = write_ding_brief(persona, "".join(tee.buf),
                              " ".join(sys.argv[1:]) or "(無參數)")
        if bp:
            print(f"📄 ding brief：{os.path.relpath(bp, REPO_ROOT)}（每次叮覆蓋）")
    return code


def _run(args, persona: str) -> int:
    """叮 catchup 本體。輸出全程被 main() 的 tee 複製進 ding brief。"""
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
    # `--quiet-system` 是 **opt-in，且不再是叮的建議用法**（Tim 2026-08-04）：
    #   酒保訊息現在多半是打款／獎金這類重要事件，不是噪音 —— 預設靜音等於預設藏錢。
    #   真的要靜音時，下面會**印出被藏了幾筆**（禁靜默截斷：藏 N 筆就要說 N）。
    last_seen = load_cursor(persona)
    unseen, hidden_system = [], 0
    for m in window:
        ts = m.get("ts", "")
        if last_seen and ts <= last_seen:
            continue
        if not args.include_self and m.get("sender_persona") == persona:
            continue  # 自己的 post 不重複給自己看
        if args.quiet_system and is_system_msg(m):
            hidden_system += 1
            continue
        unseen.append(m)

    # 區塊：輸出
    print(f"📬 叮 catchup（persona={persona}, 檢視最近 {len(window)} 筆，cursor={last_seen or '(無)'}）")
    print(format_online_line(persona))
    # 在線明細也印進 stdout（不只落 brief）—— 一行版只給名字，答不了「這名字新鮮嗎」，
    # 而 @ 一個不在線的人是靜默失敗。印在這裡＝agent 不必多跑一步就看得到。
    for name, live, agent in online_detail_rows():
        mark = "🟢" if live else ("⚪ lock 過期" if live is False else "❔")
        me = " ← 你" if name == persona else ""
        print(f"   {mark} {name}{me}" + (f"　（{agent}）" if agent else ""))
    print("   ⚠ 沒列在上面的人不要當成在線來 @（空 ≠ 沒人，只是查不到 lock）")
    print()
    if hidden_system:
        print(f"🔇 已隱藏 {hidden_system} 筆酒保系統廣播（--quiet-system）——"
              f" 打款／獎金也可能在裡面，拿掉旗標就看得到。")
        print()
    # 區塊職責：--limit 取「**最舊** N 筆」，不是最新 N 筆
    # 物理意義：catchup 是「往前追讀」的工具 —— 追讀本來就從最舊的未讀開始。
    #          🩸 初版取 unseen[-N:]（最新 N 筆）並照樣把 cursor 推到 window 末端，
    #          於是被略過的那幾筆**永久看不到**。實際代價：我用 --limit 2 撞上 4 筆未讀，
    #          略過的 2 筆裡就有 apex-one 的「撞車警告」—— 她在等我回覆，
    #          而我的工具跟我說「✓ 沒有未看過的新訊息」。
    # 數值影響：搭配下方 cursor 段 —— 略過的內容**不會**被標成已讀，下次接續顯示。
    aShown = unseen
    if not unseen:
        print(f"✓ 沒有未看過的新訊息。")
    else:
        print(f"== {len(unseen)} 筆未看訊息 ==")
        aShown = unseen if not args.limit or args.limit <= 0 else unseen[:args.limit]
        if len(aShown) < len(unseen):
            print(f"（--limit {args.limit}：印最舊 {len(aShown)} 筆；剩 {len(unseen) - len(aShown)} 筆"
                  f"**未標為已讀**，再跑一次就接著顯示）")
        for m in aShown:
            print_msg(m, full=args.full)
            print()

    # 區塊：補 context（把 ucl-ding 的「至少讀最近 N 條掌握 context」收進工具）
    # 物理意義：cursor 只給「沒看過的」，安靜的一天可能是 0 筆 —— 而叮的目的是「進 context」，
    #          不是「確認沒有新訊息」。原本 skill 要 agent 自己補跑 op=read limit=5，
    #          而「要人記得補跑」的步驟實務上就是不會跑（今天我就沒跑）。
    # 數值影響：純讀同一個 window，不動 cursor；已在上面印過的不重印。
    if args.context > 0 and len(unseen) < args.context:
        shown_ts = {m.get("ts") for m in unseen}
        pool = [m for m in window
                if m.get("ts") not in shown_ts
                and (args.include_self or m.get("sender_persona") != persona)]
        extra = pool[-(args.context - len(unseen)):] if pool else []
        if extra:
            print(f"== 補 context：另外 {len(extra)} 筆（已看過，僅供掌握近況）==")
            for m in extra:
                print_msg(m)
                print()

    # 區塊：surface durable inbox（R2 讀取端收斂）— 訊息掃描是「最近活動窗」(ephemeral)，
    #        inbox 是「未 ack 前一直在」的 durable 待辦層；兩者合成單一「我該處理什麼」視圖。
    surface_inbox(persona)


    # ===========================================================
    # 區塊：推進 cursor —— **沒顯示的不算已讀**
    # 物理意義：cursor 是一個關於「我看過什麼」的主張（apex-one 2026-08-14 的說法）。
    #          把它推過沒顯示的內容，那個主張就變成假的，而它**看起來跟真的一模一樣**：
    #          下次叮印「✓ 沒有未看過的新訊息」，語氣、格式、退出碼全部正常。
    # 數值影響：
    #   · 沒有略過任何未讀（含「本來就沒有未讀」）→ 推到 window 最大 ts（原行為）。
    #     window 內「已看過但更新」的訊息也一併涵蓋，這是刻意的。
    #   · --limit 略過了較新的未讀 → 只推到**已顯示的最新一筆**，剩下的下次接續。
    # 一致性：與 EPIPE 那條同一條規則（輸出被截斷 → 不推進）。
    #   ⚠ 初版這兩處給了相反的答案，相隔二十行 —— 而我當天正在到處說
    #     「同一問題兩套實作給相反答案」。同一個原則要在同一支檔案裡只有一個答案。
    # ===========================================================
    aSkipped = len(unseen) - len(aShown)
    if aSkipped > 0:
        aTsPool = [m.get("ts", "") for m in aShown if m.get("ts")]
        new_cursor = max(aTsPool) if aTsPool else ""
    else:
        new_cursor = max(m.get("ts", "") for m in window if m.get("ts"))
    if new_cursor and (not last_seen or new_cursor > last_seen):
        save_cursor(persona, new_cursor)
        print(f"✓ cursor 推進到 {new_cursor}"
              + (f"（只到已顯示的最新一筆 —— 尚有 {aSkipped} 筆未讀留著）" if aSkipped > 0 else ""))
    return 0


# 區塊職責：EPIPE 兜底 —— 讓「輸出被下游截斷」變成看得見的失敗
# 物理意義：下游（`| head`）提早關管線時 print 拋 BrokenPipeError，原本會讓 main 死在中途，
#          於是**末端的 cursor 推進永遠不執行**，而 pipeline 的退出碼是 head 的 0 ——
#          三件事疊起來就是「看起來成功、cursor 卻卡住」，而我當時的第一個念頭是「工具有問題」。
# 數值影響：**刻意不在 EPIPE 時推進 cursor。** 輸出已被截斷，推進等於把沒顯示的訊息標成已讀 ——
#          那是靜默丟內容，比重複顯示嚴重得多。
#          ⚠ 我原本的備忘寫「cursor 推進搬到列印前」，那個處方是錯的：它正好造成上面那件事。
#          改成「不推進 + 在 stderr 明說原因」——stderr 不會被 head 吃掉，所以人看得到。
# 邊界：退出碼用 3（與正常 0 / 參數錯 2 區隔）；但注意接了管線之後 shell 只看得到 head 的碼，
#      所以真正的訊號是 stderr 那行字，不是退出碼。
def _main_guarded() -> int:
    try:
        return main()
    except BrokenPipeError:
        try:
            for _line in (
                "",
                "⚠ 輸出被下游截斷（BrokenPipeError）—— **cursor 未推進**，這次讀到的不算已讀。",
                "   原因：`| head` 之類的下游提早關掉管線。想少讀請用本工具自己的 `--limit N`，",
                "   不要接 head —— 接了的話 pipeline 退出碼是 head 的 0，失敗會完全看不出來。",
            ):
                print(_line, file=sys.stderr)
            sys.stderr.flush()
        except Exception:
            pass
        return 3


if __name__ == "__main__":
    sys.exit(_main_guarded())
