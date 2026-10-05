"""绿证碳资产台账需求逐条自检：python3 verify_green_asset.py。

不依赖 fastapi，直接打业务层；全部断言通过才算数。
"""
from __future__ import annotations

import sys
import threading
from decimal import Decimal

from app.services.green_asset import (
    CARBON_ASSET,
    CERT_ASSET,
    LedgerError,
    service,
)
from app.store import store

PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✓ {name}")
    else:
        FAIL += 1
        print(f"  ✗ {name} {detail}")


def expect_error(name: str, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except LedgerError:
        check(name, True)
        return
    check(name, False, "（应当整批失败但没有）")


# ------------------------------------------------------------- 1. 存量回填
print("1) 存量绿证按核发月份回填（启动口径，Jan-Aug，v1 规则）")
r = service.backfill()
check("回填 2026-01 至 2026-08 共 8 个月", r["回填月份"] == [f"2026-0{m}" for m in range(1, 9)],
      str(r))
iss = {x["核发月份"]: x for x in service.list_issuances()}
jan = iss["2026-01"]
check("1 月核发绿证=8200.0 MWh（运行月报发电量口径）", jan["核发绿证"] == 8200.0, str(jan))
check("1 月碳减排量=8200×0.5703=4676.4600", jan["核发碳减排量"] == 4676.4600, str(jan))
check("核发记录冻结 v1 规则版本与因子", jan["规则版本"] == "v1" and jan["碳减排因子"] == 0.5703)
check("回填幂等：再跑一次全部跳过", service.backfill()["已存在跳过"] == [f"2026-0{m}" for m in range(1, 9)])
ledger = store.rows("green_ledger")
check("回填同步生成 8 行已关账台账", len(ledger) == 8 and all(x["口径状态"] == "已关账" for x in ledger))
check("回填后只读对账通过", service.reconcile()["ok"])

# ------------------------------------------------------- 2. 未关账月口径投影
print("2) 重新进入台账：9 月在途结算投影为未关账行，结余只从凭证明细算")
view = service.list_ledger()
months = {x["台账月份"]: x for x in view["items"]}
check("台账含 9 月投影行且为未关账", months.get("2026-09", {}).get("口径状态") == "未关账")
sep_open = months["2026-09"]
aug_end = months["2026-08"]["结余绿证"]
check("9 月期初承接 8 月结余", sep_open["期初绿证"] == aug_end)
check("9 月在途结算绿证=8000（凭证汇总，非叠加）", sep_open["结算绿证"] == 8000.0)
check("9 月投影结余=期初-在途结算", sep_open["结余绿证"] == round(aug_end - 8000, 2))

# ------------------------------------------------------- 3. 凭证号并发去重
print("3) 同一凭证并发/重复登记：只保留最初一条，结余不叠加")
entry, msg, created = service.register_settlement({
    "凭证号": "V-20260901", "资产类型": CERT_ASSET, "数量": 999.0,
    "结算月份": "2026-09", "交易对手": "重复提交"})
check("重复凭证不新建、保留最初 8000 那条", (not created) and entry["数量"] == 8000.0, msg)

created_flags: list[bool] = []
barrier = threading.Barrier(10)


def concurrent_register():
    barrier.wait()  # 10 个线程同时放行，强制在同一时刻竞争凭证号
    _, _, c = service.register_settlement({
        "凭证号": "V-CONC", "资产类型": CERT_ASSET, "数量": 100.0,
        "结算月份": "2026-09", "交易对手": "并发窗口"})
    created_flags.append(c)


threads = [threading.Thread(target=concurrent_register) for _ in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join()
conc_rows = [x for x in store.rows("green_settlement") if x["凭证号"] == "V-CONC"]
check("10 笔并发同凭证只有 1 笔落库", len(conc_rows) == 1 and sum(created_flags) == 1,
      f"rows={len(conc_rows)} created={sum(created_flags)}")
sep_open = service.list_ledger(month="2026-09")["items"][0]
check("结余未叠加：9 月在途结算=8000+100=8100", sep_open["结算绿证"] == 8100.0,
      str(sep_open["结算绿证"]))

# ------------------------------------------------------- 4. 月末事务原子性
print("4) 月末核发与结算一次事务写入；对账不平整批回滚")
before = {k: len(store.rows(k)) for k in ("green_issuance", "green_settlement", "green_ledger")}
expect_error("超卖批次（结余为负）整批失败", service.close_month, {
    "月份": "2026-09",
    "结算凭证": [
        {"凭证号": "V-20260901", "资产类型": CERT_ASSET, "数量": 8000.0, "结算月份": "2026-09"},
        {"凭证号": "V-BAD", "资产类型": CERT_ASSET, "数量": 999999.0, "结算月份": "2026-09"}]})
after = {k: len(store.rows(k)) for k in ("green_issuance", "green_settlement", "green_ledger")}
check("回滚后核发/凭证/台账行数不变", before == after, f"{before} -> {after}")
check("回滚后超卖凭证不存在", not any(x["凭证号"] == "V-BAD" for x in store.rows("green_settlement")))
expect_error("未发布月报的 10 月不能关账", service.close_month, {"月份": "2026-10"})

print("5) 规则调整发新版本：只对未核发月份生效，历史月份不回算")
rule, _ = service.add_rule({"规则版本": "v2", "生效月份": "2026-09",
                            "每证兆瓦时": 1.0, "碳减排因子": 0.6})
check("v2 规则登记成功", rule and rule["规则版本"] == "v2")
expect_error("规则版本号不允许重复", service.add_rule,
             {"规则版本": "v2", "生效月份": "2026-09", "每证兆瓦时": 1, "碳减排因子": 0.7})
res = service.close_month({"月份": "2026-09", "结算凭证": [
    {"凭证号": "V-20260903", "资产类型": CERT_ASSET, "数量": 5000.0, "结算月份": "2026-09",
     "交易对手": "绿电用户", "金额": "200000"},
    {"凭证号": "V-20260902", "资产类型": CARBON_ASSET, "数量": 4000.0, "结算月份": "2026-09"}]})
row = res["台账行"]
check("9 月按 v2 核发：绿证 9360、碳减排量 5616.0000",
      row["核发绿证"] == 9360.0 and row["核发碳减排量"] == 5616.0, str(row))
check("批次内重复凭证跳过、只新登记 1 张", res["本次新登记凭证"] == 1 and res["重复凭证跳过"] == 1)
check("9 月结算绿证=8000+100+5000=13100（重复凭证不重复计）", row["结算绿证"] == 13100.0)
check("9 月结余绿证=72950+9360-13100=69210", row["结余绿证"] == 69210.0, str(row["结余绿证"]))
jan = service.list_issuances()[0]
check("历史 1 月仍沿用 v1（4676.4600，未按 0.6 回算）", jan["核发碳减排量"] == 4676.4600)
expect_error("9 月重复关账被拒（已关账不重算）", service.close_month, {"月份": "2026-09"})
expect_error("已关账月份不再接收结算凭证", service.register_settlement,
             {"凭证号": "V-LATE", "资产类型": CERT_ASSET, "数量": 1.0, "结算月份": "2026-09"})
check("关账后三方对账通过", service.reconcile()["ok"])

# ------------------------------------------------------- 6. 导出与列表同源
print("6) 对账文件与台账列表同一口径：全量/分页/checksum 一致")
full = service.export_ledger()
p1 = service.list_ledger(page=1, size=5)
p2 = service.list_ledger(page=2, size=5)
check("分页拼起来等于全量条数", p1["total"] == full["total"] and len(p1["items"]) + len(p2["items"]) == full["total"])
check("分页页内条目与全量对应切片逐条相同",
      p1["items"] == full["items"][:5] and p2["items"] == full["items"][5:10])
ep1 = service.export_ledger(page=1, size=5)
ep2 = service.export_ledger(page=2, size=5)
check("分页打包与列表页同刀切、同 checksum（不各出一套）",
      ep1["items"] == p1["items"] and ep1["checksum"] == p1["checksum"]
      and ep2["items"] == p2["items"] and ep2["checksum"] == p2["checksum"])
check("全量 checksum 等于分页 canonical 串接",
      full["checksum"] == service._checksum(ep1["items"] + ep2["items"]))
check("总结余取末期滚动结余=9 月结余 69210", full["totals"]["总结余绿证"] == 69210.0,
      str(full["totals"]))

# 重新进入：重新取一份视图必须字节级一致
again = service.list_ledger(page=1, size=5)
check("重新进入台账同口径（checksum 不变）", again["checksum"] == p1["checksum"])

# ------------------------------------------------------- 7. 台账为唯一口径
print("7) 冲突时以台账口径为准：底层凭证被篡改时对账要报偏离")
v = next(x for x in store.rows("green_settlement") if x["凭证号"] == "V-CONC")
original = v["数量"]
v["数量"] = 999.0
bad = service.reconcile()
check("凭证侧与台账冲突被检出", (not bad["ok"]) and "对不上" in bad["mismatches"][0] or "结算" in bad["mismatches"][0],
      str(bad))
v["数量"] = original
check("恢复后对账回到一致", service.reconcile()["ok"])

print(f"\n结果：通过 {PASS}，失败 {FAIL}")
sys.exit(1 if FAIL else 0)
