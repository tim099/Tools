#!/usr/bin/env python3
# 區塊職責：退場指路 stub —— 本工具的邏輯已於 2026-08-20 搬進 C#（Tim 拍板）。
# 物理意義：實作在 `UCL_TavernCatchupService`（static class，不在 Cmd 內），
#          入口是 `Cmd_Tavern op=catchup`。
# 為什麼留 stub 而不是直接刪：Tools 是**跨專案共用 submodule**，
#          而各專案的 UCL_Core pointer 各自獨立 —— 直接刪掉會讓還沒 bump 的專案
#          在叮的第一步就 FileNotFoundError，而那個錯誤不會告訴他該去哪。
#          ⇒ 指路 stub 讓「舊呼叫」變成**明確的指路**，不是靜默失敗。
# 數值影響：exit 2；不讀不寫任何狀態。
#
# 🩸 搬家的真正理由（不是「比較乾淨」）：
#   「已讀到哪」原本有三個寫入端 —— C# UCL_TavernCursor / python tavern_cmd.py /
#   本檔，各自 read-modify-write 同一份 `_inbox_cursor/<persona>.json`。
#   2026-08-16 觀影 sidecar 的兩隻游標 bug 就是這個家族，而兩次都「看起來正常」。
import sys

MSG = """\
⛔ tavern_catchup.py 已退場（2026-08-20）—— 邏輯搬進 C#，游標從此只有一個寫入端。

改跑：
  python <UCL_Core>/Tools~/AgentCommands/run_cmd.py --persona <me> run Tavern \
      --arg op=catchup --arg persona=<me>

  可選：--arg min=10（至少讀幾筆）／--arg quiet_system=0（含酒保廣播）
        --arg include_self=1（含自己）／--arg advance=0（**不推游標**，只看）

回傳檔仍是 letters/<persona>/cmd/ding_brief.md（路徑沒變，skill 照舊 Read 它）。
實作：UCL_TavernCatchupService（static class）；入口：Cmd_Tavern.Op_Catchup。
"""

if __name__ == "__main__":
    sys.stderr.write(MSG)
    sys.exit(2)
