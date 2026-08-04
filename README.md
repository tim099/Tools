---
title: AgentCommands/Tools — Python CLI 工具索引
description: 22 個 Python CLI 工具 + 一個 crypto helper module 的 one-liner 索引, 跨 agent 找工具不用 grep
last_updated: 2026-05-16
target_audience: [AI_Agent (Claude / Antigravity / Gemini / Zeta), Tim]
created_by: calli (claude-code), work session ws-20260516T082717Z-e8e0
---

# AgentCommands/Tools — Python CLI 工具索引

> 22 個 Python CLI 工具 + 1 crypto helper module。每條走 `python AgentCommands/Tools/<name>.py <args>` 呼叫；多半 standalone 不靠 Unity Editor。
>
> 工具動工原則：能用工具的場景**禁直編** JSON (relations / treasury / tavern messages) — 直接 IO 違反 schema, 走工具 wrap 才能保 audit trail。
>
> 找工具 SOP：先掃本表 → 命中 `name` 直接 `--help` 看完整 CLI → 沒有則考慮新做工具 (而非手寫 inline script)。

---

## 💰 Treasury / Economy


> [!IMPORTANT]
> **財務相關操作一律走 Cmd（C# server 端），python 不直寫**（Tim 2026-08-04 定調）。
> 已於 2026-08-04 移除三支直寫 ledger 的工具：
> `gold_convert.py`（EoV 專案的，不屬本 repo）、
> `qa_bug_reward.py`（對應 skill 已移除）、
> `treasury_revert.py`（revert 屬 Bank 後台操作且應走請款單，不該是 python 工具）。
>
> 動錢請走 `Cmd_Treasury`（`op=credit` / `debit` / `transfer_request` / `request`）
> 或 `UCL_BankAdminPage`。理由：直寫會繞過冪等判重、`sig_*` 簽章、
> 以及**餘額快取的增量維護** —— 後者會讓 Editor 看到的餘額與磁碟不一致且無錯誤訊息。

| 工具 | 一句話 | 對應 spec / skill |
|---|---|---|
| [balance_query.py](balance_query.py) | 查任一 actor / agent / persona Treasury 餘額 (ledger source-of-truth) | T41 ledger spec |
| [migrate_voucher_v1_to_v2.py](migrate_voucher_v1_to_v2.py) | 酒館券資料格式 v1 → v2 遷移 | one-shot migration |

## 🎭 Persona / Affinity / Identity

| 工具 | 一句話 | 對應 spec / skill |
|---|---|---|
| [affinity_update.py](affinity_update.py) | 更新 persona ↔ 對象 emotion_vector (8 軸) — **禁直編 relations.json** | ucl-affinity skill |
| [persona_character_clone.py](persona_character_clone.py) | T03 — 從模板 clone RCG_CharacterData (ID / HP× / 自介) | Persona_Character_Workflow |
| [persona_ding.py](persona_ding.py) | persona ↔ persona 自叮 (同 actor 內) | ucl-persona-ding skill |
| [build_tavern_identities.py](build_tavern_identities.py) | 從 RCG_CharacterData 生成 tavern persona identity manifest | identity sync |

## 🍻 Tavern / Communication

| 工具 | 一句話 | 對應 spec / skill |
|---|---|---|
| [tavern_query.py](tavern_query.py) | T56 — Read-only 酒館訊息查詢 (不走 Cmd_Tavern) | T56 spec |
| [discord_inbound_bot.py](discord_inbound_bot.py) | Discord channel → Tavern 中繼 daemon (gateway listener) | Discord_Inbound_Workflow |

## 📋 Task / Work Session

| 工具 | 一句話 | 對應 spec / skill |
|---|---|---|
| [agent_task.py](agent_task.py) | T60 — Reverse task system: Agent → Tim 提案, Tim Y/N 接受 | agent-task skill |

## 🩺 QA / Balance / Debug

| 工具 | 一句話 | 對應 spec / skill |
|---|---|---|
| [qa_balance_report.py](qa_balance_report.py) | T07 — QA Battle Balance Report Aggregator | Plan_QA_Battle_Balance_Workflow |
| [qa_record_battle.py](qa_record_battle.py) | T03 — Battle Result Recorder | Plan_QA_Battle_Balance_Workflow |
| [qa_score_card.py](qa_score_card.py) | T04 — Card Power Scorer | Plan_QA_Battle_Balance_Workflow |
| [workflow_patch.py](workflow_patch.py) | Register workflow patch entry (≥3 patches → refactor 警示) | ucl-workflow-patch skill |
| [debuglog_query.py](debuglog_query.py) | T03 — DebugLog 結構化查詢 (取代手動 grep multi-log) | DebugLog_Query_Workflow |

## ☀️ Morning / Status

| 工具 | 一句話 | 對應 spec / skill |
|---|---|---|
| [morning_status.py](morning_status.py) | T59 — Tim 早晨 dashboard，彙整 token / Tavern 活動 / inbox | dashboard |

## 🔐 Secrets

| 工具 | 一句話 | 對應 spec / skill |
|---|---|---|
| [secret_install.py](secret_install.py) | Secret encrypt / decrypt / status CLI (passphrase-based, Fernet) | _secrets/ workflow |
| [secrets_crypto.py](secrets_crypto.py) | `import` helper module (KDF 200k iter, AES-128-CBC + HMAC-SHA256) — non-CLI | (used by secret_install) |

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

- ❌ **禁直編** schema 走的 JSON (relations.json / treasury ledger / tavern messages) — 一律走工具 wrap
- ❌ **禁手寫 inline 腳本** 做重複動作 — 該寫進 Tools/ 才是長期 fix
- ✅ 新工具 ship 時補本 README + 該對應的 Workflow / Plan 文件

— calli, claude-code, 2026-05-16 (work session ws-e8e0 wt-005)
