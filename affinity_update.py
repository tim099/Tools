#!/usr/bin/env python3
# 區塊職責：退場指路 stub（Tim 2026-08-18）。
# 物理意義：好感度系統已改名並改架構為 **relationship**，資料搬到
#          `letters/<persona>/relationship/<target>/`（一事件一檔）。
#          本檔原本直寫 `ChatTavern/affinity/<persona>/relations.json` ——
#          那個倉庫已凍結，**寫進去的東西不會被任何人看到，而且不會報錯**。
# ⛔ 為什麼是 stub 不是直接刪：刪掉的話舊 session／別的專案打過來只會得到
#   `No such file`，那句話不告訴任何人該改用什麼。用一個會說話的失敗，
#   換掉一個沉默的失敗。
# 數值影響：不做任何事，exit 2。
import sys

MSG = """\
⛔ affinity_update.py 已退場（2026-08-18）—— 好感度系統改名為 relationship。

寫一筆事件請改用 Cmd 通用接口（沒有 python 包裝層）：

  python <UCL_Core>/Tools~/AgentCommands/run_cmd.py --persona <me> run Relationship \
      --arg op=update --arg persona=<me> --arg target=<對誰> \
      --arg reason="<這件事是什麼>" \
      --arg trust=0.05 --arg respect=0.03 --arg admiration=0.02 \
      --arg opinion="<內心戲短句，選填>"

其餘 op：add-opinion / show / list / rebuild

完整說明 → skill `ucl-relationship`
規格與維護流程 → ucl_core:Docs~/{lang}/Mechanics/Relationship_System.md

⚠ 舊資料 `ChatTavern/affinity/` 已凍結為對照組，不要再寫入。
"""

if __name__ == "__main__":
    print(MSG, file=sys.stderr)
    sys.exit(2)
