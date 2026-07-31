#!/usr/bin/env python3
"""
T59 morning_status.py — Tim 早晨 dashboard，一行看完一切

⚠ 這不是 awakening ritual 工具（2026-07-31 Sirius 誤用後加註）。
   早安 / 晚安儀式的唯一入口是：
       python <UCL_Core>/Tools~/AgentCommands/awakening.py morning --agent <A> --persona <P>
       python <UCL_Core>/Tools~/AgentCommands/awakening.py goodnight --persona <P>
   本檔只是 Tim 個人看板（HP / token / 24h 活動彙整），不寫任何 persona 狀態、
   不發 token、不做 persona lock —— 拿它當 ritual 會「跑完什麼都沒發生」而且不會報錯。
   （source-side guard：與其要每個 skill 先做 preflight，不如讓被誤認的這支自己聲明身分。）

職責：Low-effort 模式的 cognitive load reducer — 跑一次看整體狀態。
物理意義：彙整 HP / token / 24h tavern 活動 / 今日結算 / 待辦提示，
        Tim 不必腦補多 source 資料。

範例:
  python AgentCommands/Tools/morning_status.py
  python AgentCommands/Tools/morning_status.py --verbose   # 包含詳細 ledger entries
"""

import argparse
import datetime
import glob
import json
import os
import subprocess
import sys

# Windows utf-8 fix
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass


LEDGER_ROOT = "AgentCommands/Treasury/ledger"
TAVERN_ROOT = "AgentCommands/ChatTavern/rooms"


def calc_balance(account):
    """區塊職責：scan ledger 算指定 account 的 tavern_token balance"""
    total_c = 0
    total_d = 0
    for f in sorted(glob.glob(f"{LEDGER_ROOT}/*/*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                e = json.load(fp)
        except (OSError, json.JSONDecodeError):
            continue
        if e.get("account_id") != account:
            continue
        if e.get("currency", "tavern_token") != "tavern_token":
            continue
        if e.get("type") == "credit":
            total_c += e.get("amount", 0)
        else:
            total_d += e.get("amount", 0)
    return total_c - total_d


def today_token_changes(account):
    """區塊職責：算 Tim 今日 (UTC date) tavern_token 進出"""
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    today_path = f"{LEDGER_ROOT}/{today}"
    if not os.path.isdir(today_path):
        return [], 0, 0
    entries = []
    credit_total = 0
    debit_total = 0
    for fname in sorted(os.listdir(today_path)):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(today_path, fname), "r", encoding="utf-8") as f:
                e = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        if e.get("account_id") != account:
            continue
        entries.append(e)
        amt = e.get("amount", 0)
        if e.get("type") == "credit":
            credit_total += amt
        else:
            debit_total += amt
    return entries, credit_total, debit_total


def tavern_active_rooms_24h():
    """區塊職責：呼叫 tavern_query.py 取 24h 活躍房 raw"""
    try:
        result = subprocess.run(
            [sys.executable, "AgentCommands/Tools/tavern_query.py", "rooms", "--since", "24h"],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        return result.stdout.strip()
    except Exception as e:
        return f"(query fail: {e})"


def get_recent_tavern_messages(limit=5):
    """區塊職責：跨房 timeline 最近 N 則"""
    try:
        result = subprocess.run(
            [sys.executable, "AgentCommands/Tools/tavern_query.py", "timeline",
             "--since", "24h", "--limit", str(limit)],
            capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        return result.stdout.strip()
    except Exception as e:
        return f"(query fail: {e})"


def get_pending_inbox(account):
    """區塊職責：列出 account inbox 未讀（看 mtime > X 小時）"""
    inbox_paths = glob.glob(f"AgentCommands/ChatTavern/rooms/*/inbox/{account}.md")
    pending = []
    for p in inbox_paths:
        if not os.path.exists(p):
            continue
        size = os.path.getsize(p)
        mtime = os.path.getmtime(p)
        if size > 0:
            pending.append((p, mtime, size))
    return pending


def main():
    # 區塊職責：CLI 介面 + 身分自證
    # 物理意義：本檔曾被誤認為 awakening ritual 工具（2026-07-31 Sirius）。誤用者第一個動作
    #          通常是 --help，所以警語要進 argparse 而不是只放 module docstring。
    # 數值影響：純輸出文字，不改行為。
    _EPILOG = (
        "WARNING 早安/晚安儀式的唯一入口是 <UCL_Core>/Tools~/AgentCommands/awakening.py" + chr(10) +
        "   morning:   python <UCL_Core>/Tools~/AgentCommands/awakening.py morning --agent <A> --persona <P>" + chr(10) +
        "   goodnight: python <UCL_Core>/Tools~/AgentCommands/awakening.py goodnight --persona <P>" + chr(10) +
        "本檔只是 Tim 個人看板：不寫 persona 狀態、不發 token、不做 persona lock。"
    )
    parser = argparse.ArgumentParser(
        description="T59 Tim morning dashboard — 這不是 awakening ritual 工具",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--verbose", action="store_true", help="印詳細 ledger entries")
    parser.add_argument("--account", default="Tim", help="預設 Tim")
    args = parser.parse_args()

    now = datetime.datetime.now()
    print("=" * 72)
    print(f"  🌅 Morning Status — {now.strftime('%Y-%m-%d %H:%M:%S')} (local)")
    print("=" * 72)

    # ─── Section 1: Treasury ───
    balance = calc_balance(args.account)
    print(f"\n## 💰 Treasury")
    print(f"  {args.account} balance: **{balance}** tavern_token")
    bartender = calc_balance("tavern-keeper")
    print(f"  bartender (酒保): {bartender} tavern_token")

    entries, credit_total, debit_total = today_token_changes(args.account)
    print(f"\n## 📜 今日 Token 流水 ({len(entries)} 筆)")
    print(f"  credit total : +{credit_total}")
    print(f"  debit total  : -{debit_total}")
    print(f"  net          : {credit_total - debit_total:+d}")

    if args.verbose and entries:
        print(f"\n  詳細 entries:")
        for e in entries[-10:]:
            ts = e.get("ts", "")[:19]
            kind = e.get("source_kind") or e.get("use_kind") or "?"
            tp = e.get("type", "?")
            amt = e.get("amount", 0)
            sign = "+" if tp == "credit" else "-"
            print(f"    {ts} {sign}{amt:>3} {kind:<28} {(e.get('source_description') or '')[:40]}")

    # ─── Section 2: Tavern 活動 ───
    print(f"\n## 🏨 Tavern 24h 活躍房")
    rooms_out = tavern_active_rooms_24h()
    # 縮減 output 只保前 5 行
    for line in rooms_out.split("\n")[:8]:
        print(f"  {line}")

    if args.verbose:
        print(f"\n## ⏱ 跨房最近 5 則訊息")
        timeline_out = get_recent_tavern_messages(5)
        for line in timeline_out.split("\n")[:8]:
            print(f"  {line}")

    # ─── Section 3: Inbox ───
    pending = get_pending_inbox(args.account)
    if pending:
        print(f"\n## 📬 {args.account} Inbox")
        for p, mtime, size in pending:
            mtime_str = datetime.datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M")
            # 區塊職責：跨 platform 拆 room name (Windows \\ vs Unix /)
            # path: .../ChatTavern/rooms/<ROOM>/inbox/Tim.md
            inbox_dir = os.path.dirname(p)        # .../<ROOM>/inbox
            room = os.path.basename(os.path.dirname(inbox_dir))   # <ROOM>
            print(f"  {mtime_str}  {room:<30}  {size:>5} bytes")

    # ─── Section 4: 提示 ───
    print(f"\n## 💡 提示")
    if balance == 0:
        print(f"  - tavern_token 0 餘額 — 派 task 會 hard stop fee；建議先補足餘額")
    elif balance < 5:
        print(f"  - tavern_token 偏低 ({balance})，派 task 前注意 fee")

    print()


if __name__ == "__main__":
    main()
