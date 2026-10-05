"""绿证碳资产台账接口。

口径约定（列表、导出、重新进入都遵守）：
- 台账行 green_ledger 是唯一口径源；
- /ledger 分页与 /ledger/export 走同一个 service._view，全量打包和分页打包不会各出一套；
- /ledger/export.csv 的数也取自同一份视图，文件头带 checksum，可与列表响应对账。
"""
from __future__ import annotations

import io
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.schemas import ActionResult
from app.services.green_asset import (
    CARBON_ASSET,
    CERT_ASSET,
    LedgerError,
    service,
)

router = APIRouter(prefix="/api/green_asset", tags=["绿证碳资产台账"])


class SettlementPayload(BaseModel):
    """交易结算登记：凭证号是财务侧唯一键。"""

    凭证号: str
    资产类型: str = Field(description=f"{CERT_ASSET} / {CARBON_ASSET}")
    数量: float
    结算月份: str
    交易对手: str | None = None
    金额: str | None = None


class RulePayload(BaseModel):
    """核发规则新版本：调整规则只新发版本，不动已核发月份。"""

    规则版本: str
    生效月份: str
    每证兆瓦时: float
    碳减排因子: float
    算法: str | None = None


class CloseMonthPayload(BaseModel):
    """月末关账：核发 + 本批结算在一次事务里一起写入。"""

    月份: str
    结算凭证: list[SettlementPayload] = Field(default_factory=list)


class BackfillPayload(BaseModel):
    """存量绿证回填：按核发月份逐月用当时规则补发。"""

    截止月份: str | None = None


def _raise(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


# ------------------------------------------------------------------ 台账口径

@router.get("/ledger")
def list_ledger(
    month: str | None = Query(default=None, description="按台账月份 YYYY-MM 精确筛选"),
    status: str | None = Query(default=None, description="已关账 / 未关账"),
    page: int = 1,
    size: int = 20,
) -> dict[str, Any]:
    """台账列表：分页读取，响应里带 totals 与 checksum，导出文件必须同口径。

    刻意不挂 response_model：分页字段之外还有 totals/checksum/columns/口径，
    被响应模型裁掉就没法和对账文件对口径了。
    """
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    return service.list_ledger(month=month, status=status, page=page, size=size)


@router.get("/ledger/export")
def export_ledger(
    month: str | None = None,
    status: str | None = None,
    page: int | None = Query(default=None, description="不传=全量打包；传了=与列表同一刀分页打包"),
    size: int | None = None,
) -> dict[str, Any]:
    """对账文件数据体：与台账列表同一个函数取数，checksum 可与列表逐页比对。"""
    return service.export_ledger(month=month, status=status, page=page, size=size)


@router.get("/ledger/export.csv")
def export_ledger_csv(month: str | None = None, status: str | None = None) -> StreamingResponse:
    """CSV 对账文件：数同样来自台账视图，头部注释写 checksum，文件与列表不会各算各的。"""
    view = service.export_ledger(month=month, status=status)
    columns = view["columns"]
    buf = io.StringIO()
    buf.write(f"# 绿证碳资产台账对账文件（口径：{view['口径']}）\n")
    buf.write(f"# 行数={view['total']} checksum={view['checksum']}\n")
    buf.write(",".join(columns) + "\n")
    for row in view["items"]:
        buf.write(",".join(str(row.get(col, "")) for col in columns) + "\n")
    data = "﻿" + buf.getvalue()
    return StreamingResponse(
        io.BytesIO(data.encode("utf-8")),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=green_asset_ledger.csv"},
    )


@router.get("/reconcile")
def reconcile() -> dict[str, Any]:
    """只读对账：以台账口径为准，返回两侧是否对得平。"""
    return service.reconcile()


# -------------------------------------------------------------------- 核发侧

@router.get("/rules")
def list_rules() -> dict[str, Any]:
    return {"items": service.list_rules()}


@router.post("/rules", response_model=ActionResult)
def add_rule(payload: RulePayload) -> ActionResult:
    """登记核发规则新版本；已核发月份不随新规则重算。"""
    try:
        rule, missing = service.add_rule(payload.model_dump())
    except LedgerError as exc:
        raise _raise(exc)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message=f"规则 {rule['规则版本']} 已登记，自 {rule['生效月份']} 起对新核发月份生效", entry=rule)


@router.get("/issuances")
def list_issuances() -> dict[str, Any]:
    """逐月核发记录：里面冻结了核发当时的规则版本与参数。"""
    return {"items": service.list_issuances()}


@router.post("/close_month", response_model=ActionResult)
def close_month(payload: CloseMonthPayload) -> ActionResult:
    """月末关账：核发与结算一次事务写入，对账不平整批失败。"""
    try:
        result = service.close_month({
            "月份": payload.月份,
            "结算凭证": [item.model_dump() for item in payload.结算凭证],
        })
    except LedgerError as exc:
        raise _raise(exc)
    return ActionResult(ok=True, message=result.pop("message"), entry=result)


@router.post("/backfill", response_model=ActionResult)
def backfill(payload: BackfillPayload) -> ActionResult:
    """存量绿证按核发月份回填；已回填月份沿用原算法，不重复补发。"""
    try:
        result = service.backfill(payload.截止月份)
    except LedgerError as exc:
        raise _raise(exc)
    return ActionResult(ok=True, message=result.pop("message"), entry=result)


# -------------------------------------------------------------------- 财务侧

@router.get("/settlements")
def list_settlements(voucher: str | None = None) -> dict[str, Any]:
    return {"items": service.list_settlements(voucher)}


@router.post("/settlements", response_model=ActionResult)
def register_settlement(payload: SettlementPayload) -> ActionResult:
    """登记交易结算凭证：同凭证号重复登记只保留最初那条，结余不叠加。

    重复提交属于幂等命中而非失败：HTTP 200、ok=true，由 message 说明保留的是最初一条。
    """
    try:
        entry, message, _created = service.register_settlement(payload.model_dump())
    except LedgerError as exc:
        raise _raise(exc)
    return ActionResult(ok=True, message=message, entry=entry)
