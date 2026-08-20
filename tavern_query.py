#!/usr/bin/env python3
# 區塊職責：退場指路 stub —— 本工具的邏輯已於 2026-08-20 搬進 C#（Tim 拍板）。
# 物理意義：實作在 `UCL_TavernQueryService`（static class，不在 Cmd 內），
#          入口是 `Cmd_Tavern op=query --arg kind=...`。
# 為什麼留 stub 而不是直接刪：見 tavern_catchup.py 同段（跨專案共用 submodule）。
# 數值影響：exit 2；不讀不寫任何狀態。
#
# 🩸 順帶記一筆讀數：搬家前用 python 跑 `stats --since 6h` **超過 2 分鐘沒跑完**
#   （它自己走訪全部訊息、無快取）；C# 版走 UCL_ChatTavernIO 的既有快取，秒級回來。
import sys

MSG = """\
⛔ tavern_query.py 已退場（2026-08-20）—— 邏輯搬進 C#。

改跑：
  python <UCL_Core>/Tools~/AgentCommands/run_cmd.py --persona <me> run Tavern \
      --arg op=query --arg persona=<me> --arg kind=<kind> [...]

  kind=rooms      [--arg since=24h]
  kind=tail       --arg room=tavern --arg limit=20
  kind=search     --arg keyword=<字串> [--arg room=] [--arg since=] [--arg case_sensitive=1]
  kind=by_sender  --arg sender=<id 或 persona> [--arg since=] [--arg limit=]
  kind=timeline   [--arg since=24h] [--arg limit=30]
  kind=stats      [--arg since=24h]
  kind=seq        --arg room=tavern [--arg seq=N | --arg from=A --arg to=B]
                  [--arg last=N] [--arg sender_persona=] [--arg sender=] [--arg tag=]
                  [--arg grep=<regex>] [--arg full=1]

帶 persona 時回傳檔落 letters/<persona>/cmd/tavern_query.md；不帶則落 _last_op.md。
實作：UCL_TavernQueryService（static class）；入口：Cmd_Tavern.Op_Query。
"""

if __name__ == "__main__":
    sys.stderr.write(MSG)
    sys.exit(2)
