"""绿证碳资产台账业务规则。

一本账，四张表：
- green_rule       核发规则（版本化）：每证兆瓦时、碳减排因子；规则只对之后核发的月份生效
- green_issuance   逐月核发记录：核发时把规则参数原样冻结进来，规则再调也不回算
- green_settlement 交易结算凭证：凭证号全表唯一，重复登记保留最初一条
- green_ledger     月度台账行（期初/核发/结算/结余）：列表、导出、重新进入都只读这一份口径

所有写入都包在 store.transaction() 里：月末关账把核发、结算、台账行一次写完，
写完两侧对账（运维核发侧 = 财务凭证侧 = 台账结余），对不上就整批回滚。
"""
from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app.store import store

MODULE_RULE = "green_rule"
MODULE_ISSUANCE = "green_issuance"
MODULE_SETTLEMENT = "green_settlement"
MODULE_LEDGER = "green_ledger"
MODULE_REPORT = "report"

MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
CERT_ASSET = "绿证"
CARBON_ASSET = "碳减排量"
ASSET_TYPES = (CERT_ASSET, CARBON_ASSET)
CLOSED = "已关账"
OPEN = "未关账"

Q2 = Decimal("0.01")
Q4 = Decimal("0.0001")
EPS = Decimal("0.000001")


class LedgerError(ValueError):
    """台账业务校验失败：在事务内抛出会触发整批回滚。"""


def _d(value: Any) -> Decimal:
    try:
        return Decimal(str(value).strip())
    except (ArithmeticError, AttributeError, ValueError) as exc:
        raise LedgerError(f"数值「{value}」无法参与台账核算") from exc


def _q(value: Decimal, quant: Decimal) -> float:
    return float(value.quantize(quant, rounding=ROUND_HALF_UP))


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _published_report(month: str) -> dict[str, Any] | None:
    for row in store.rows(MODULE_REPORT):
        if str(row.get("统计月份")) == month and row.get("status") == "已发布":
            return row
    return None


def _settled(month: str, asset: str) -> Decimal:
    return sum(
        (_d(row["数量"]) for row in store.rows(MODULE_SETTLEMENT)
         if str(row.get("结算月份")) == month and row.get("资产类型") == asset),
        Decimal("0"),
    )


class GreenAssetService:
    # ------------------------------------------------------------------ 规则

    def list_rules(self) -> list[dict[str, Any]]:
        return sorted(store.rows(MODULE_RULE), key=lambda r: str(r.get("生效月份", "")))

    def add_rule(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [f for f in ("规则版本", "生效月份", "每证兆瓦时", "碳减排因子")
                   if str(values.get(f) or "").strip() == ""]
        if missing:
            return None, missing
        version = str(values["规则版本"]).strip()
        effective_from = str(values["生效月份"]).strip()
        if not MONTH_RE.match(effective_from):
            raise LedgerError("生效月份必须是 YYYY-MM 格式")
        mwh_per_cert, carbon_factor = _d(values["每证兆瓦时"]), _d(values["碳减排因子"])
        if mwh_per_cert <= 0 or carbon_factor <= 0:
            raise LedgerError("每证兆瓦时与碳减排因子必须为正数")
        with store.transaction():
            if any(str(r.get("规则版本")) == version for r in store.rows(MODULE_RULE)):
                raise LedgerError(f"规则版本 {version} 已存在；调整规则请发新版本，不要覆盖旧版本")
            rule = {
                "id": store.next_id(MODULE_RULE),
                "规则版本": version,
                "生效月份": effective_from,
                "每证兆瓦时": _q(mwh_per_cert, Q4),
                "碳减排因子": _q(carbon_factor, Q4),
                "算法": str(values.get("算法") or
                             "绿证=发电量MWh/每证兆瓦时；碳减排量=发电量MWh×碳减排因子"),
                "登记时间": _now(),
            }
            store.rows(MODULE_RULE).append(rule)
        return rule, []

    def _effective_rule(self, month: str) -> dict[str, Any]:
        """取该核发月份当时生效的规则：生效月份不晚于目标月份里最新的一条。"""
        candidates = [r for r in store.rows(MODULE_RULE)
                      if str(r.get("生效月份", "")) <= month]
        if not candidates:
            raise LedgerError(f"{month} 没有生效中的核发规则，无法核发")
        return sorted(candidates, key=lambda r: str(r["生效月份"]))[-1]

    def _issue(self, month: str) -> dict[str, Any]:
        """按运行月报发电量口径核发；规则参数快照进核发记录，之后不再回算。"""
        if any(str(r.get("核发月份")) == month for r in store.rows(MODULE_ISSUANCE)):
            raise LedgerError(f"{month} 已核发过绿证，核发记录不重复生成")
        report = _published_report(month)
        if report is None:
            raise LedgerError(f"{month} 没有已发布的运行月报，不能按月报发电量口径核发")
        try:
            kwh = _d(report.get("发电量"))
        except LedgerError:
            raise LedgerError(f"{month} 运行月报发电量不是合法数值，无法核发")
        if kwh <= 0:
            raise LedgerError(f"{month} 运行月报发电量必须为正数")
        rule = self._effective_rule(month)
        mwh = kwh / Decimal("1000")
        cert = (mwh / _d(rule["每证兆瓦时"])).quantize(Q2, rounding=ROUND_HALF_UP)
        carbon = (mwh * _d(rule["碳减排因子"])).quantize(Q4, rounding=ROUND_HALF_UP)
        issuance = {
            "id": store.next_id(MODULE_ISSUANCE),
            "核发月份": month,
            "月报编号": report.get("月报编号"),
            "发电量kWh": _q(kwh, Q2),
            "发电量MWh": _q(mwh, Q4),
            "核发绿证": float(cert),
            "核发碳减排量": float(carbon),
            "规则版本": rule["规则版本"],
            "每证兆瓦时": rule["每证兆瓦时"],
            "碳减排因子": rule["碳减排因子"],
            "算法": rule["算法"],
            "核发时间": _now(),
        }
        store.rows(MODULE_ISSUANCE).append(issuance)
        return issuance

    # ------------------------------------------------------------------ 结算

    def list_settlements(self, voucher: str | None = None) -> list[dict[str, Any]]:
        rows = store.rows(MODULE_SETTLEMENT)
        if voucher:
            rows = [r for r in rows if voucher in str(r.get("凭证号", ""))]
        return sorted(rows, key=lambda r: int(r.get("id", 0)))

    def _insert_settlement(self, values: dict[str, Any]) -> tuple[dict[str, Any], bool, str]:
        """插入一张结算凭证。返回(记录, 是否新登记, 说明)。

        必须在事务+锁内调用：凭证号查重和写入是复合操作，靠同一把锁串行化，
        并发登记同凭证号时只有先到的一条落库，结余天然不会被叠加。
        """
        voucher = str(values.get("凭证号") or "").strip()
        asset = str(values.get("资产类型") or "").strip()
        month = str(values.get("结算月份") or "").strip()
        for row in store.rows(MODULE_SETTLEMENT):
            if str(row.get("凭证号")) == voucher:
                return row, False, f"凭证 {voucher} 已登记过，保留最初登记，本次不重复入账"
        missing = [f for f in ("凭证号", "资产类型", "数量", "结算月份")
                   if not str(values.get(f) or "").strip()]
        if missing:
            raise LedgerError(f"结算登记缺少必填字段：{'、'.join(missing)}")
        if asset not in ASSET_TYPES:
            raise LedgerError(f"资产类型只能是 {CERT_ASSET} 或 {CARBON_ASSET}")
        if not MONTH_RE.match(month):
            raise LedgerError("结算月份必须是 YYYY-MM 格式")
        qty = _d(values["数量"])
        if qty <= 0:
            raise LedgerError("结算数量必须为正数，卖出/注销请用资产类型区分，不接受负数")
        if any(str(r.get("台账月份")) == month for r in store.rows(MODULE_LEDGER)):
            raise LedgerError(f"{month} 已月末关账，台账行已冻结，不再接收结算凭证")
        entry = {
            "id": store.next_id(MODULE_SETTLEMENT),
            "凭证号": voucher,
            "资产类型": asset,
            "数量": _q(qty, Q4 if asset == CARBON_ASSET else Q2),
            "结算月份": month,
            "交易对手": str(values.get("交易对手") or "").strip(),
            "金额": str(values.get("金额") or "").strip(),
            "登记时间": _now(),
            "status": "已登记",
            "pending": False,
            "abnormal": False,
        }
        store.rows(MODULE_SETTLEMENT).append(entry)
        return entry, True, f"凭证 {voucher} 已登记，结算数量 {entry['数量']}"

    def register_settlement(self, values: dict[str, Any]) -> tuple[dict[str, Any], str, bool]:
        """单笔结算登记：独立接口，事务内做凭证号去重。"""
        with store.transaction():
            entry, created, message = self._insert_settlement(values)
            if created:
                self._verify()
        return entry, message, created

    # -------------------------------------------------------------- 月末关账

    def close_month(self, values: dict[str, Any]) -> dict[str, Any]:
        """月末核发与结算在一次事务里一起写入，写完两侧对账，不过整批失败。"""
        month = str(values.get("月份") or "").strip()
        if not MONTH_RE.match(month):
            raise LedgerError("关账月份必须是 YYYY-MM 格式")
        batch = values.get("结算凭证") or []
        if not isinstance(batch, list):
            raise LedgerError("结算凭证必须是数组")
        with store.transaction():
            if any(str(r.get("台账月份")) == month for r in store.rows(MODULE_LEDGER)):
                raise LedgerError(f"{month} 已关账，已关账月份不重算、不补记")
            for item in batch:
                if str(item.get("结算月份") or "").strip() not in ("", month):
                    raise LedgerError("关账批次只接受本月结算凭证，跨月凭证请先逐月登记")
                item.setdefault("结算月份", month)
            # 1) 运维侧：按月报发电量核发绿证与碳减排量（规则参数冻结）
            issuance = self._issue(month)
            # 2) 财务侧：批次凭证按凭证号去重后落库（已存在的保留最初那条）
            created, skipped = 0, 0
            for item in batch:
                _, is_new, _ = self._insert_settlement(item)
                created += int(is_new)
                skipped += int(not is_new)
            # 3) 台账行：结余全部从明细汇总，绝不做增量叠加
            prev = self._prev_ending(month)
            row = {
                "id": store.next_id(MODULE_LEDGER),
                "台账月份": month,
                "口径状态": CLOSED,
                "规则版本": issuance["规则版本"],
                "关账时间": _now(),
                "pending": False,
                "abnormal": False,
            }
            self._fill_balance_row(row, month, prev, issuance)
            store.rows(MODULE_LEDGER).append(row)
            # 4) 两侧对账：核发侧、凭证侧、台账结余三方一致且不存在超卖，否则整批回滚
            self._verify()
        return {
            "台账行": row,
            "核发": issuance,
            "本次新登记凭证": created,
            "重复凭证跳过": skipped,
            "message": f"{month} 已关账：核发绿证 {row['核发绿证']}、"
                       f"碳减排量 {row['核发碳减排量']}，对账一致",
        }

    def _prev_ending(self, month: str) -> dict[str, Decimal]:
        rows = sorted(
            (r for r in store.rows(MODULE_LEDGER) if str(r["台账月份"]) < month),
            key=lambda r: str(r["台账月份"]),
        )
        if not rows:
            return {CERT_ASSET: Decimal("0"), CARBON_ASSET: Decimal("0")}
        last = rows[-1]
        return {CERT_ASSET: _d(last["结余绿证"]), CARBON_ASSET: _d(last["结余碳减排量"])}

    def _fill_balance_row(self, row: dict[str, Any], month: str,
                          prev: dict[str, Decimal], issuance: dict[str, Any]) -> None:
        issued = {CERT_ASSET: _d(issuance["核发绿证"]), CARBON_ASSET: _d(issuance["核发碳减排量"])}
        settled = {asset: _settled(month, asset) for asset in ASSET_TYPES}
        for asset, prefix in ((CERT_ASSET, "绿证"), (CARBON_ASSET, "碳减排量")):
            ending = prev[asset] + issued[asset] - settled[asset]
            row[f"期初{prefix}"] = _q(prev[asset], Q4)
            row[f"核发{prefix}"] = _q(issued[asset], Q4)
            row[f"结算{prefix}"] = _q(settled[asset], Q4)
            row[f"结余{prefix}"] = _q(ending, Q4)

    def _verify(self) -> None:
        """三方对账：核发记录 ↔ 结算凭证 ↔ 台账结余。任何一笔不平都抛错回滚。"""
        mismatches: list[str] = []
        issuances = {str(r["核发月份"]): r for r in store.rows(MODULE_ISSUANCE)}
        ledger_rows = sorted(store.rows(MODULE_LEDGER), key=lambda r: str(r["台账月份"]))

        seen_months: set[str] = set()
        for row in ledger_rows:
            month = str(row["台账月份"])
            if month in seen_months:
                mismatches.append(f"{month} 出现两条台账行")
            seen_months.add(month)
            issue = issuances.get(month)
            if issue is None:
                mismatches.append(f"{month} 台账已关账但缺少核发记录")
                continue
            if _d(row["核发绿证"]) != _d(issue["核发绿证"]):
                mismatches.append(f"{month} 核发绿证与核发记录不一致")
            if _d(row["核发碳减排量"]) != _d(issue["核发碳减排量"]):
                mismatches.append(f"{month} 核发碳减排量与核发记录不一致")
            for asset, prefix in ((CERT_ASSET, "绿证"), (CARBON_ASSET, "碳减排量")):
                settled = _settled(month, asset)
                if abs(_d(row[f"结算{prefix}"]) - settled) > EPS:
                    mismatches.append(f"{month} 结算{prefix}与凭证明细汇总不一致（台账叠加错位）")
                expect_end = _d(row[f"期初{prefix}"]) + _d(row[f"核发{prefix}"]) - settled
                if abs(_d(row[f"结余{prefix}"]) - expect_end) > EPS:
                    mismatches.append(f"{month} 结余{prefix}不满足 期初+核发-结算")
                if _d(row[f"结余{prefix}"]) < -EPS:
                    mismatches.append(f"{month} 结余{prefix}为负，卖出量超过结存量")

        prev_month = ""
        prev: dict[str, Decimal] = {CERT_ASSET: Decimal("0"), CARBON_ASSET: Decimal("0")}
        for row in ledger_rows:
            month = str(row["台账月份"])
            for asset, prefix in ((CERT_ASSET, "绿证"), (CARBON_ASSET, "碳减排量")):
                if prev_month and abs(_d(row[f"期初{prefix}"]) - prev[asset]) > EPS:
                    mismatches.append(f"{month} 期初{prefix}未承接上月结余")
            prev = {CERT_ASSET: _d(row["结余绿证"]), CARBON_ASSET: _d(row["结余碳减排量"])}
            prev_month = month

        if ledger_rows:
            closed_months = {str(r["台账月份"]) for r in ledger_rows}
            closed_issues = [r for r in issuances.values()
                             if str(r["核发月份"]) in closed_months]
            closed_settlements = [r for r in store.rows(MODULE_SETTLEMENT)
                                  if str(r["结算月份"]) in closed_months]
            total = {
                CERT_ASSET: sum((_d(r["核发绿证"]) for r in closed_issues), Decimal("0"))
                - sum((_d(r["数量"]) for r in closed_settlements
                       if r["资产类型"] == CERT_ASSET), Decimal("0")),
                CARBON_ASSET: sum((_d(r["核发碳减排量"]) for r in closed_issues), Decimal("0"))
                - sum((_d(r["数量"]) for r in closed_settlements
                       if r["资产类型"] == CARBON_ASSET), Decimal("0")),
            }
            last = ledger_rows[-1]
            if abs(_d(last["结余绿证"]) - total[CERT_ASSET]) > EPS:
                mismatches.append("累计绿证结余与「累计核发-累计结算」对不上")
            if abs(_d(last["结余碳减排量"]) - total[CARBON_ASSET]) > EPS:
                mismatches.append("累计碳减排量结余与「累计核发-累计结算」对不上")

        if mismatches:
            raise LedgerError("月末对账失败，整批回滚：" + "；".join(mismatches[:5]))

    def reconcile(self) -> dict[str, Any]:
        """只读对账：以台账口径为准，报告核发侧/凭证侧有没有偏离台账。"""
        try:
            with store.lock:
                self._verify()
        except LedgerError as exc:
            return {"ok": False, "口径": "以绿证碳资产台账为准", "mismatches": [str(exc)]}
        return {"ok": True, "口径": "以绿证碳资产台账为准", "mismatches": []}

    # ------------------------------------------------------------ 存量回填

    def backfill(self, through: str | None = None, *,
                 include_latest: bool = False) -> dict[str, Any]:
        """存量绿证按核发月份回填：逐月用当时生效规则核发，整笔事务一次成型。

        through 指定回填截止月份；不指定时默认回填到「最近一个已发布月份」之前，
        把最新月份留给在途结算与月末关账（include_latest=True 可连最新月一起补）。
        """
        if through is not None and not MONTH_RE.match(through):
            raise LedgerError("回填截止月份必须是 YYYY-MM 格式")
        with store.transaction():
            published = sorted({str(r["统计月份"]) for r in store.rows(MODULE_REPORT)
                                if r.get("status") == "已发布"
                                and MONTH_RE.match(str(r.get("统计月份", "")))})
            if not published:
                raise LedgerError("没有已发布的运行月报，存量绿证无发电量口径可回填")
            if through is not None:
                cutoff = through
            elif include_latest:
                cutoff = published[-1]
            else:
                cutoff = published[-2] if len(published) >= 2 else ""
            months = [m for m in published if not cutoff or m <= cutoff]
            done, skipped = [], []
            for month in months:
                if any(str(r.get("核发月份")) == month for r in store.rows(MODULE_ISSUANCE)):
                    skipped.append(month)
                    continue
                issuance = self._issue(month)
                prev = self._prev_ending(month)
                row = {
                    "id": store.next_id(MODULE_LEDGER),
                    "台账月份": month,
                    "口径状态": CLOSED,
                    "规则版本": issuance["规则版本"],
                    "关账时间": _now(),
                    "pending": False,
                    "abnormal": False,
                }
                self._fill_balance_row(row, month, prev, issuance)
                store.rows(MODULE_LEDGER).append(row)
                done.append(month)
            self._verify()
        return {"回填月份": done, "已存在跳过": skipped, "截止月份": cutoff,
                "message": f"存量回填 {len(done)} 个月，跳过已回填 {len(skipped)} 个月"}

    # ---------------------------------------------------------------- 台账口径

    def _open_month(self) -> str | None:
        """最早一个「有已发布月报或有在途结算、但还没关账」的月份。"""
        closed = {str(r["台账月份"]) for r in store.rows(MODULE_LEDGER)}
        candidates = {
            str(r["统计月份"]) for r in store.rows(MODULE_REPORT)
            if r.get("status") == "已发布" and MONTH_RE.match(str(r.get("统计月份", "")))
        }
        candidates |= {
            str(r["结算月份"]) for r in store.rows(MODULE_SETTLEMENT)
            if MONTH_RE.match(str(r.get("结算月份", "")))
        }
        pending = sorted(m for m in candidates if m not in closed)
        return pending[0] if pending else None

    def _canonical_rows(self, *, month: str | None = None,
                        status: str | None = None) -> list[dict[str, Any]]:
        """台账唯一读口径。列表分页、全量打包、CSV 导出都只能从这里取数。"""
        rows = sorted(
            (dict(r) for r in store.rows(MODULE_LEDGER)),
            key=lambda r: str(r["台账月份"]),
        )
        open_month = self._open_month()
        if open_month and (not month or month == open_month):
            issuance = next(
                (r for r in store.rows(MODULE_ISSUANCE)
                 if str(r.get("核发月份")) == open_month), None)
            projected = {
                "id": f"open-{open_month}",
                "台账月份": open_month,
                "口径状态": OPEN,
                "规则版本": issuance["规则版本"] if issuance else self._effective_rule_safe(open_month),
                "核发绿证": 0.0,
                "核发碳减排量": 0.0,
                "关账时间": "",
                "pending": True,
                "abnormal": False,
            }
            prev = self._prev_ending(open_month)
            for asset, prefix in ((CERT_ASSET, "绿证"), (CARBON_ASSET, "碳减排量")):
                q = Q2 if asset == CERT_ASSET else Q4
                settled = _settled(open_month, asset)
                projected[f"期初{prefix}"] = _q(prev[asset], Q4)
                projected[f"核发{prefix}"] = 0.0
                projected[f"结算{prefix}"] = _q(settled, Q4)
                projected[f"结余{prefix}"] = _q(prev[asset] - settled, q)
            rows.append(projected)
        if month:
            rows = [r for r in rows if str(r.get("台账月份")) == month]
        if status:
            rows = [r for r in rows if r.get("口径状态") == status]
        return rows

    def _effective_rule_safe(self, month: str) -> str:
        try:
            return str(self._effective_rule(month)["规则版本"])
        except LedgerError:
            return ""

    @staticmethod
    def _columns() -> list[str]:
        return ["台账月份", "口径状态", "规则版本",
                "期初绿证", "核发绿证", "结算绿证", "结余绿证",
                "期初碳减排量", "核发碳减排量", "结算碳减排量", "结余碳减排量",
                "关账时间"]

    def _view(self, *, month: str | None = None, status: str | None = None,
              page: int = 1, size: int = 100000) -> dict[str, Any]:
        """列表与导出的共用实现：先取全量有序行、算总口径与校验摘要，再切同一刀。"""
        all_rows = self._canonical_rows(month=month, status=status)
        # 总结余只取最晚月份行的期末结余：各行结余本身是滚动存量，逐行相加会重复累计。
        last_row = all_rows[-1] if all_rows else None
        totals = {
            "累计核发绿证": round(sum(float(r["核发绿证"]) for r in all_rows), 2),
            "累计核发碳减排量": round(sum(float(r["核发碳减排量"]) for r in all_rows), 4),
            "累计结算绿证": round(sum(float(r["结算绿证"]) for r in all_rows), 2),
            "累计结算碳减排量": round(sum(float(r["结算碳减排量"]) for r in all_rows), 4),
            "总结余绿证": round(float(last_row["结余绿证"]), 2) if last_row else 0.0,
            "总结余碳减排量": round(float(last_row["结余碳减排量"]), 4) if last_row else 0.0,
        }
        checksum = self._checksum(all_rows)
        total = len(all_rows)
        page_no = max(page, 1)
        size_no = max(size, 1)
        start = (page_no - 1) * size_no
        return {
            "columns": self._columns(),
            "items": all_rows[start:start + size_no],
            "total": total,
            "page": page_no,
            "size": size_no,
            "totals": totals,
            "checksum": checksum,
            "口径": "以绿证碳资产台账为准；列表、分页导出、全量导出同源",
        }

    def list_ledger(self, *, month: str | None = None, status: str | None = None,
                    page: int = 1, size: int = 20) -> dict[str, Any]:
        return self._view(month=month, status=status, page=page, size=size)

    def export_ledger(self, *, month: str | None = None, status: str | None = None,
                      page: int | None = None, size: int | None = None) -> dict[str, Any]:
        """对账文件数据体：不传分页即全量打包；传分页则与列表页同一刀切结果。"""
        if page is None or size is None:
            return self._view(month=month, status=status)
        return self._view(month=month, status=status, page=page, size=size)

    @staticmethod
    def _checksum(rows: list[dict[str, Any]]) -> str:
        import hashlib
        import json
        body = json.dumps(rows, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), default=str)
        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    def list_issuances(self) -> list[dict[str, Any]]:
        return sorted(store.rows(MODULE_ISSUANCE), key=lambda r: str(r.get("核发月份", "")))


service = GreenAssetService()
