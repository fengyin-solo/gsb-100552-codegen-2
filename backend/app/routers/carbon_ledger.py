"""绿证碳资产台账接口。

关键点：列表分页、全量导出、分页打包都调用同一个 service.query_entries，
返回的口径指纹（caliber_fingerprint）也来自同一份全量结果，因此不可能
"各出一套结果"。
"""
from __future__ import annotations

import csv
import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from app.schemas import (
    ActionResult,
    BackfillPayload,
    MonthEndPayload,
    PageResult,
    RulePayload,
    SettlementPayload,
)
from app.services.carbon_ledger import LedgerError, carbon_ledger_service

router = APIRouter(prefix="/api/carbon-ledger", tags=["绿证碳资产台账"])

COLUMNS = [
    "台账编号", "类型", "归属月份", "凭证号/核发编号", "月报编号", "对手方",
    "绿证数量(个)", "碳减排量(t)", "结算金额(元)", "规则版本", "登记时间",
]


def _filters(
    month: str | None,
    entry_type: str | None,
    keyword: str | None,
) -> dict[str, str | None]:
    if entry_type and entry_type not in ("核发", "结算"):
        raise HTTPException(status_code=400, detail="类型只支持「核发」「结算」")
    return {"month": month, "entry_type": entry_type, "keyword": keyword}


@router.get("/entries", response_model=PageResult[dict])
def list_entries(
    month: str | None = Query(default=None, description="按归属月份 YYYY-MM 过滤"),
    entry_type: str | None = Query(default=None, alias="type", description="核发 / 结算"),
    keyword: str | None = Query(default=None, description="凭证号、核发编号、月报编号、对手方"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """台账列表：分页切片只影响返回范围，口径指纹对的是全量结果。"""
    if page < 1 or size < 1:
        raise HTTPException(status_code=400, detail="page/size 必须为正整数")
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条")
    kwargs = _filters(month, entry_type, keyword)
    rows = carbon_ledger_service.query_entries(**kwargs)
    start = (page - 1) * size
    page_rows = rows[start:start + size]
    # 指纹基于全量行计算，与分页无关：翻到任何一页都能和对账文件对同一份口径
    return PageResult(
        items=page_rows,
        total=len(rows),
        page=page,
        size=size,
        caliber=carbon_ledger_service.caliber_fingerprint(rows),
    )


@router.get("/export")
def export_pack(
    month: str | None = None,
    entry_type: str | None = Query(default=None, alias="type"),
    keyword: str | None = None,
    page: int | None = Query(default=None, description="传 page+size 即分页打包；不传为全量打包"),
    size: int = 20,
) -> dict[str, Any]:
    """对账文件（JSON）：全量打包与分页打包共用 query_entries，指纹相同即为同一份口径。"""
    kwargs = _filters(month, entry_type, keyword)
    pack = carbon_ledger_service.reconciliation_pack(**kwargs)
    pack["打包方式"] = "全量"
    if page is not None:
        if page < 1 or size < 1 or size > 200:
            raise HTTPException(status_code=400, detail="page 必须为正整数、size 不超过 200")
        start = (page - 1) * size
        pack["明细"] = pack["明细"][start:start + size]
        pack["打包方式"] = f"分页(第{page}页/每页{size}条)"
        pack["页码"] = page
    return pack


@router.get("/export.csv")
def export_csv(
    month: str | None = None,
    entry_type: str | None = Query(default=None, alias="type"),
    keyword: str | None = None,
) -> Response:
    """对账文件（CSV 下载）：与 JSON 对账包同一次 query_entries 结果，口径一致。"""
    kwargs = _filters(month, entry_type, keyword)
    pack = carbon_ledger_service.reconciliation_pack(**kwargs)
    buf = io.StringIO()
    buf.write(f"# 口径：{pack['口径']}\n# 口径指纹：{pack['口径指纹']}\n")
    writer = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in pack["明细"]:
        writer.writerow(row)
    balances = pack["结余"]
    buf.write(
        f"# 结余：累计核发{balances['累计核发绿证']}个 / 累计交割{balances['累计交割绿证']}个"
        f" / 结余{balances['结余绿证']}个；碳减排结余{balances['结余碳减排']}t\n"
    )
    return Response(
        content="﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="carbon_ledger.csv"'},
    )


@router.get("/summary")
def summary() -> dict[str, Any]:
    """汇总结余：结余始终由流水推导，是台账口径的直接读数。"""
    return carbon_ledger_service.summary()


@router.get("/rules")
def list_rules() -> dict[str, Any]:
    return {"items": carbon_ledger_service.list_rules()}


@router.post("/rules", response_model=ActionResult)
def add_rule(payload: RulePayload) -> ActionResult:
    """登记新的核发规则版本；只对未核发月份生效，已核发月份不重算。"""
    try:
        rule = carbon_ledger_service.add_rule(payload.model_dump())
    except LedgerError as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(ok=True, message=f"规则 {rule['规则版本']} 已登记，自 {rule['生效月份']} 起生效", entry=rule)


@router.post("/backfill", response_model=ActionResult)
def backfill(payload: BackfillPayload) -> ActionResult:
    """存量绿证按核发月份回填，整批事务写入，任一月份不合法则整批失败。"""
    try:
        result = carbon_ledger_service.backfill(payload.items)
    except LedgerError as exc:
        return ActionResult(ok=False, message=f"回填整批失败：{exc}")
    return ActionResult(ok=True, message=f"已按核发月份回填 {result['回填月份数']} 个月", entry=result)


def _settlement_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """把接口模型的字段名翻译成台账内部口径字段。"""
    return {
        "凭证号": raw.get("凭证号"),
        "归属月份": raw.get("归属月份"),
        "交易日期": raw.get("交易日期"),
        "对手方": raw.get("对手方"),
        "绿证交割量(个)": raw.get("绿证交割量", 0),
        "碳减排交割量(t)": raw.get("碳减排交割量", 0),
        "结算金额(元)": raw.get("结算金额", 0),
    }


@router.post("/settlements", response_model=ActionResult)
def register_settlement(payload: SettlementPayload) -> ActionResult:
    """登记一笔结算：凭证号重复（含并发）时只保留最初那条，结余不会叠加。"""
    try:
        row, message = carbon_ledger_service.register_settlement(
            _settlement_dict(payload.model_dump())
        )
    except LedgerError as exc:
        return ActionResult(ok=False, message=str(exc))
    duplicated = message.startswith("凭证号") and "最初记录" in message
    return ActionResult(ok=not duplicated, message=message, entry=row)


@router.post("/month-end-close", response_model=ActionResult)
def month_end_close(payload: MonthEndPayload) -> ActionResult:
    """月末核发 + 结算一次事务写入；两边对不上整批失败、已全部回滚。"""
    try:
        result = carbon_ledger_service.month_end_close(
            month=payload.月份,
            report_no=payload.月报编号,
            generation_mwh=payload.发电量MWh,
            settlements=[_settlement_dict(item.model_dump()) for item in payload.结算明细],
            finance_balance=payload.财务填报结余,
        )
    except LedgerError as exc:
        return ActionResult(ok=False, message=str(exc))
    snap = result["结余"]
    message = (
        f"{snap['月份']} 已月末关账：核发 {snap['当月核发绿证']} 个、"
        f"交割 {snap['当月交割绿证']} 个、结余 {snap['结余绿证']} 个，状态：{snap['对平状态']}"
    )
    if result["重复凭证"]:
        dupes = "、".join(dict.fromkeys(result["重复凭证"]))
        message += f"；重复凭证已去重：{dupes}"
    return ActionResult(ok=True, message=message, entry=result)
