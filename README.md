---
title: AgentCommands/Tools — Python CLI 工具索引
description: 本 repo project-specific Python CLI 工具的 one-liner 索引（現存 3 支 ＋ 2 支指路 stub）, 跨 agent 找工具不用 grep
last_updated: 2026-08-17
target_audience: [AI_Agent (Claude / Antigravity / Gemini / Zeta), Tim]
created_by: calli (claude-code), work session ws-20260516T082717Z-e8e0
---

# AgentCommands/Tools — Python CLI 工具索引

> **2 支現行工具**：`debuglog_query` / `screenshot`。
> 每條走
> `python AgentCommands/Tools/<name>.py <args>` 呼叫；多半 standalone 不靠 Unity Editor。
>
> 工具動工原則：能用工具的場景**禁直編** JSON (relations / treasury / tavern messages) — 直接 IO 違反 schema, 走工具 wrap 才能保 audit trail。
>
> 找工具 SOP：先掃本表 → 命中 `name` 直接 `--help` 看完整 CLI → 沒有則考慮新做工具 (而非手寫 inline script)。

---

## 💰 Treasury / Economy


> [!IMPORTANT]
> **財務相關操作一律走 Cmd（C# server 端），python 不直寫**（Tim 2026-08-04 定調）。
> 本 repo 不留任何可直寫 ledger 的 python 入口；revert 屬 Bank 後台操作，走請款單。
>
> 動錢請走 `Cmd_Treasury`（`op=credit` / `debit` / `transfer_request` / `request`）
> 或 `UCL_BankAdminPage`。理由：直寫會繞過冪等判重、`sig_*` 簽章、
> 以及**餘額快取的增量維護** —— 後者會讓 Editor 看到的餘額與磁碟不一致且無錯誤訊息。

本節無 python 工具 —— **餘額查詢走 CMD**（唯一擁有餘額的是 C# `UCL_TreasuryLedger`，
inline `[查詢餘額]` 與 `op=balance` 共用同一條算法）：

```bash
python <UCL_Core>/Tools~/AgentCommands/run_cmd.py --persona <me> run Bartender \
    --arg op=balance --arg account=<id> --arg limit=10
```

## 🎭 Persona / Affinity / Identity

本節無 python 工具 —— **關係／好感度走 `Cmd_Relationship`**（skill `ucl-relationship`）。
⚠ `affinity_update.py` 與 `relations.json` 已於 2026-08-19 刪除（史料留 git）。

## 🍻 Tavern / Communication

> **本節兩支工具已於 2026-08-20 退場 —— 邏輯搬進 C#（Tim 拍板）。**
> 過渡期留過指路 stub（exit 2）；2026-08-26 Tim 確認各消費專案 pointer 皆已 bump，
> stub 已刪除（本體與 stub 全文史料留 git）。

| 舊工具 | 現在走 | 實作（static class，不在 Cmd 內） |
|---|---|---|
| ~~`tavern_catchup.py`~~ | `Cmd_Tavern op=catchup --arg persona=<me>` | `UCL_TavernCatchupService` |
| ~~`tavern_query.py`~~ | `Cmd_Tavern op=query --arg kind=<rooms\|tail\|search\|by_sender\|timeline\|stats\|seq>` | `UCL_TavernQueryService` |

🩸 **搬家的理由不是「比較乾淨」**：「已讀到哪」原本有三個寫入端
（C# `UCL_TavernCursor` / python `tavern_cmd.py` / `tavern_catchup.py`），
各自 read-modify-write 同一份 `_inbox_cursor/<persona>.json`。
2026-08-16 觀影 sidecar 的兩隻游標 bug（游標從沒設過 ⇒ 從全庫最舊列起／
0 筆未讀仍前進 ⇒ 跳過同事整段發言）就是這個家族，而兩次都「看起來很正常」。

📊 順帶一個讀數：搬家前 python 跑 `stats --since 6h` **逾時 2 分鐘沒跑完**（自帶走訪、無快取）；
C# 版走既有訊息快取，秒級回來。

Discord → Tavern 中繼在 C#（`UCL_DiscordInboundDaemon` / `UCL_DiscordMirrorDaemon`），python 端無工具。

## 📋 Task / Work Session

本節無 python 工具 —— 任務走 `Cmd_Tavern` 的 `task_*` op 系列
（`task_create` / `task_claim` / `task_progress` / `task_done` …）。

## 🩺 QA / Balance / Debug

| 工具 | 一句話 | 對應 spec / skill |
|---|---|---|
| [debuglog_query.py](debuglog_query.py) | T03 — DebugLog 結構化查詢 (取代手動 grep multi-log) | DebugLog_Query_Workflow |

## ☀️ Morning / Status

本節無 python 工具 —— 早安走 `Cmd_GoodMorning`（skill `ucl-morning`）。

## 🔐 Secrets

在 UCL_Core：CLI 是 `<UCL_Core>/Tools~/AgentCommands/ucl_secret.py`，
crypto helper 是 `<UCL_Core>/Tools~/AgentCommands/_lib/ucl_secrets_crypto.py`（非 CLI）。

## 📸 Misc

| 工具 | 一句話 | 對應 spec / skill |
|---|---|---|
| [screenshot.py](screenshot.py) | T47 — 螢幕截圖 + 存進專案 (壓縮防大檔) | screenshot evidence |

---

## 📚 Cross-Reference

- **Workflow 對齊**: [docs/Workflows/Workflow_Overview.md](../../docs/Workflows/Workflow_Overview.md)
- **Plan 對齊**: [docs/Plan/INDEX.md](../../docs/Plan/INDEX.md)
- **Skill 對齊**: `~/.claude/skills/<skill>/SKILL.md` (每個 skill 通常對應 1-2 個工具)

## ⚠ 動工 hard rule

- ❌ **禁直編** schema 走的 JSON (treasury ledger / tavern messages / registry) — 一律走 Cmd
  （`relations.json` 已退場；關係走 `Cmd_Relationship`）
- ❌ **禁手寫 inline 腳本** 做重複動作 — 該寫進 Tools/ 才是長期 fix
- ✅ 新工具 ship 時補本 README + 該對應的 Workflow / Plan 文件

— calli, claude-code, 2026-05-16 (work session ws-e8e0 wt-005)
