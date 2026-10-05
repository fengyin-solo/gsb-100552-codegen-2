"""绿证碳资产台账的端到端校验。

覆盖需求：
- 按运行月报发电量核发绿证与碳减排量（规则版本快照）
- 规则调整后已核发月份不重算，继续沿用原算法
- 列表/全量导出/分页打包同源、口径指纹一致
- 同一凭证重复登记只保留最初那条（含并发）、结余不叠加
- 月末核发与结算一次事务写入，对不平整批失败
- 存量绿证按核发月份回填（整批事务）
- 重启（重新加载文件）后仍是同一份口径
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

from app import ledger_store  # noqa: E402
from app.main import app  # noqa: E402
from app.services.carbon_ledger import CarbonLedgerService  # noqa: E402

client = TestClient(app)


@pytest.fixture()
def service(tmp_path):
    """每个用例一份独立账本文件，并把模块单例切过去。"""
    path = tmp_path / "ledger.json"
    store = ledger_store.LedgerStore(str(path))
    store.load()
    ledger_store.ledger_store = store
    svc = CarbonLedgerService()
    return svc


# ---- 核发口径 --------------------------------------------------------------

def test_issuance_uses_generation_caliber(service):
    result = service.month_end_close(
        month="2026-08", report_no="REPO-202608", generation_mwh=1000,
    )
    issued = result["核发"]
    assert issued["绿证核发量(个)"] == 1000.0          # 1 MWh = 1 个绿证
    assert issued["碳减排量(tCO2e)"] == 570.3         # 1000 × 0.5703
    assert issued["规则版本"] == "v2024"
    assert result["结余"]["结余绿证"] == 1000.0
    assert result["结余"]["结余碳减排"] == 570.3


def test_issuance_snapshot_rule_and_never_recompute(service):
    # 8 月用 v2024 核发
    service.month_end_close(month="2026-08", report_no="R1", generation_mwh=1000)
    # 9 月起调整规则：2 MWh 核发 1 个绿证，减排因子改为 0.6
    service.add_rule({
        "规则版本": "v2026", "生效月份": "2026-09",
        "绿证系数": 0.5, "减排因子": 0.6,
    })
    service.month_end_close(month="2026-09", report_no="R2", generation_mwh=2000)

    tables = service.store.snapshot()
    aug = next(r for r in tables["issuance"] if r["核发月份"] == "2026-08")
    sep = next(r for r in tables["issuance"] if r["核发月份"] == "2026-09")
    assert aug["规则版本"] == "v2024" and aug["绿证核发量(个)"] == 1000.0
    assert sep["规则版本"] == "v2026" and sep["绿证核发量(个)"] == 1000.0
    assert sep["碳减排量(tCO2e)"] == 1200.0

    # 已核发月份再核发/回填一律拒绝，绝不重算
    with pytest.raises(Exception, match="已核发"):
        service.month_end_close(month="2026-08", report_no="R1", generation_mwh=1000)
    with pytest.raises(Exception, match="已核发"):
        service.backfill([{"核发月份": "2026-08", "发电量(MWh)": 1000}])


# ---- 凭证号幂等 + 结余不叠加 ------------------------------------------------

def test_duplicate_voucher_keeps_first(service):
    service.backfill([{"核发月份": "2026-07", "发电量(MWh)": 1000}])
    row1, msg1 = service.register_settlement({
        "凭证号": "V-001", "归属月份": "2026-08", "绿证交割量(个)": 100,
    })
    row2, msg2 = service.register_settlement({
        "凭证号": "V-001", "归属月份": "2026-08", "绿证交割量(个)": 100,
    })
    assert row1["id"] == row2["id"]
    assert "最初记录" in msg2
    assert len(service.store.snapshot()["settlement"]) == 1
    assert service.summary()["结余"]["结余绿证"] == 900.0   # 只扣一次，没叠加


def test_concurrent_settlements_dedup_by_voucher(service):
    service.backfill([{"核发月份": "2026-07", "发电量(MWh)": 1000}])

    def register(i):
        # 20 个线程用同一个凭证号并发登记
        return service.register_settlement({
            "凭证号": "V-RACE", "归属月份": "2026-08", "绿证交割量(个)": 100,
        })

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(register, range(20)))
    assert len(service.store.snapshot()["settlement"]) == 1
    assert len({row["id"] for row, _ in results}) == 1
    assert service.summary()["结余"]["结余绿证"] == 900.0


# ---- 月末事务原子性 ----------------------------------------------------------

def test_month_end_close_balances_and_writes_together(service):
    result = service.month_end_close(
        month="2026-08", report_no="R1", generation_mwh=1000,
        settlements=[
            {"凭证号": "V-1", "绿证交割量": 300},
            {"凭证号": "V-2", "绿证交割量": 200},
            {"凭证号": "V-1", "绿证交割量": 300},   # 批内重复 → 去重
        ],
        finance_balance=500,
    )
    tables = service.store.snapshot()
    assert [r["凭证号"] for r in tables["settlement"]] == ["V-1", "V-2"]
    assert result["重复凭证"] == ["V-1"]
    assert result["结余"]["结余绿证"] == 500.0
    assert tables["months"][0]["对平状态"] == "已对平"


def test_month_end_close_whole_batch_rolls_back_on_mismatch(service):
    service.month_end_close(month="2026-07", report_no="R0", generation_mwh=100)
    before = service.store.snapshot()
    with pytest.raises(Exception, match="两边对不上"):
        service.month_end_close(
            month="2026-08", report_no="R1", generation_mwh=1000,
            settlements=[{"凭证号": "V-1", "绿证交割量": 200}],
            finance_balance=999,
        )
    after = service.store.snapshot()
    # 核发、结算、月末快照全部没有留下
    assert after == before
    assert not any(r["核发月份"] == "2026-08" for r in after["issuance"])
    assert after["settlement"] == []
    assert len(after["months"]) == 1


def test_month_end_close_rejects_oversell(service):
    with pytest.raises(Exception, match="对不平"):
        service.month_end_close(
            month="2026-08", report_no="R1", generation_mwh=100,
            settlements=[{"凭证号": "V-1", "绿证交割量": 150}],
        )
    assert service.store.snapshot()["issuance"] == []


def test_month_end_close_must_include_preregistered_settlements(service):
    service.backfill([{"核发月份": "2026-07", "发电量(MWh)": 1000}])
    # 关账前先预登记一笔 8 月结算
    service.register_settlement({
        "凭证号": "PRE-1", "归属月份": "2026-08", "绿证交割量(个)": 100,
    })
    # 关账批次漏掉它 → 整批失败
    with pytest.raises(Exception, match="未纳入本次关账"):
        service.month_end_close(
            month="2026-08", report_no="R1", generation_mwh=500,
            settlements=[{"凭证号": "OTHER", "绿证交割量": 50}],
        )
    # 带上它（仍只保留最初那条记录）后正常关账，结余与流水完全一致
    result = service.month_end_close(
        month="2026-08", report_no="R1", generation_mwh=500,
        settlements=[
            {"凭证号": "PRE-1", "绿证交割量": 100},
            {"凭证号": "OTHER", "绿证交割量": 50},
        ],
    )
    assert result["结余"]["结余绿证"] == 1350.0
    assert [r["凭证号"] for r in service.store.snapshot()["settlement"]] == ["PRE-1", "OTHER"]


# ---- 存量回填 ---------------------------------------------------------------

def test_backfill_by_issuance_month_uses_rules_at_that_time(service):
    service.add_rule({
        "规则版本": "v2025", "生效月份": "2025-01",
        "绿证系数": 1.0, "减排因子": 0.5810,
    })
    result = service.backfill([
        {"核发月份": "2024-06", "发电量(MWh)": 500, "月报编号": "HIS-06"},
        {"核发月份": "2025-03", "发电量(MWh)": 800, "月报编号": "HIS-03"},
    ])
    assert result["回填月份数"] == 2
    tables = service.store.snapshot()
    jun = next(r for r in tables["issuance"] if r["核发月份"] == "2024-06")
    mar = next(r for r in tables["issuance"] if r["核发月份"] == "2025-03")
    assert jun["规则版本"] == "v2024" and jun["碳减排量(tCO2e)"] == 285.15
    assert mar["规则版本"] == "v2025" and mar["碳减排量(tCO2e)"] == 464.8
    assert all(r["来源"] == "存量回填" for r in tables["issuance"])


def test_backfill_all_or_nothing(service):
    with pytest.raises(Exception, match="2025-03"):
        service.backfill([
            {"核发月份": "2025-01", "发电量(MWh)": 500},
            {"核发月份": "2025-03", "发电量(MWh)": 0},  # 非法 → 整批失败
        ])
    assert service.store.snapshot()["issuance"] == []


# ---- 同一口径：列表 / 全量包 / 分页包 ----------------------------------------

def test_list_export_paginated_share_one_caliber(service):
    service.backfill([
        {"核发月份": f"2025-{m:02d}", "发电量(MWh)": 100} for m in range(1, 7)
    ])
    for i in range(3):
        service.register_settlement({
            "凭证号": f"V-{i+1}", "归属月份": "2025-06", "绿证交割量": 50,
        })

    full_rows = service.query_entries()
    fp = service.caliber_fingerprint(full_rows)

    page1 = client.get("/api/carbon-ledger/entries", params={"page": 1, "size": 3}).json()
    page2 = client.get("/api/carbon-ledger/entries", params={"page": 2, "size": 3}).json()
    assert page1["caliber"] == page2["caliber"] == fp

    pack_full = client.get("/api/carbon-ledger/export").json()
    assert pack_full["口径指纹"] == fp
    assert pack_full["总条数"] == len(full_rows)

    # 把分页包拼回来，明细与全量包逐行一致
    packs = []
    for page in range(1, 4):
        p = client.get("/api/carbon-ledger/export", params={"page": page, "size": 3}).json()
        assert p["口径指纹"] == fp          # 指纹对的是全量，不会另出一套
        packs.extend(p["明细"])
    assert packs == pack_full["明细"]


def test_filtered_export_matches_filtered_list(service):
    service.backfill([
        {"核发月份": "2025-01", "发电量(MWh)": 100},
        {"核发月份": "2025-02", "发电量(MWh)": 100},
    ])
    service.register_settlement({"凭证号": "V-1", "归属月份": "2025-01", "绿证交割量": 30})
    list_resp = client.get("/api/carbon-ledger/entries", params={"month": "2025-01"}).json()
    pack = client.get("/api/carbon-ledger/export", params={"month": "2025-01"}).json()
    assert [r["台账编号"] for r in pack["明细"]] == [r["台账编号"] for r in list_resp["items"]]
    assert pack["口径指纹"] == list_resp["caliber"]


# ---- 持久化：重新进入仍是同一份口径 ------------------------------------------

def test_persistence_across_reload(tmp_path):
    path = tmp_path / "ledger.json"
    store = ledger_store.LedgerStore(str(path))
    store.load()
    ledger_store.ledger_store = store
    svc = CarbonLedgerService()
    svc.month_end_close(
        month="2026-08", report_no="R1", generation_mwh=1000,
        settlements=[{"凭证号": "V-1", "绿证交割量": 200}],
    )
    fp_before = svc.caliber_fingerprint(svc.query_entries())

    # 模拟重启：新 store 从同一个文件加载
    store2 = ledger_store.LedgerStore(str(path))
    store2.load()
    ledger_store.ledger_store = store2
    svc2 = CarbonLedgerService()
    assert svc2.caliber_fingerprint(svc2.query_entries()) == fp_before
    assert svc2.summary()["结余"]["结余绿证"] == 800.0


# ---- API 层 -----------------------------------------------------------------

def test_api_settlement_dedup_and_message(service):
    service.backfill([{"核发月份": "2026-07", "发电量(MWh)": 1000}])
    payload = {"凭证号": "V-9", "归属月份": "2026-08", "绿证交割量": 100}
    first = client.post("/api/carbon-ledger/settlements", json=payload).json()
    second = client.post("/api/carbon-ledger/settlements", json=payload).json()
    assert first["ok"] is True
    assert second["ok"] is False and "最初记录" in second["message"]
    assert first["entry"]["id"] == second["entry"]["id"]


def test_api_closed_month_rejects_new_settlement(service):
    service.month_end_close(month="2026-08", report_no="R1", generation_mwh=1000)
    resp = client.post("/api/carbon-ledger/settlements", json={
        "凭证号": "V-X", "归属月份": "2026-08", "绿证交割量": 10,
    }).json()
    assert resp["ok"] is False and "关账" in resp["message"]
