"""绿证碳资产台账业务规则。

口径约定（整本台账唯一的事实源）：
1. 按运行月报的发电量核发：绿证 = 发电量(MWh) × 绿证系数；碳减排量 = 发电量 × 减排因子。
2. 核发规则按生效月份版本化。核发时把规则版本、系数、因子一起快照到核发记录上；
   规则调整只新增版本，已核发月份永不重算，继续沿用签发当时的算法。
3. 结余永远由流水逐笔推导：累计核发 - 累计交割，任何地方都不做增量累加，
   因此重复/并发登记不可能把结余叠加两次。
4. 列表、分页、全量导出、对账文件共用 query_entries 这一个查询口径。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from app import ledger_store as ledger_store_module
from app.ledger_store import ledger_store

# 数量统一保留到 0.0001 个/t，金额保留到分
QTY = Decimal("0.0001")
CASH = Decimal("0.01")


class LedgerError(ValueError):
    """台账业务校验失败：月末整批写入时抛出即代表整批回滚。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _month(value: Any, field: str = "月份") -> str:
    text = str(value or "").strip()
    try:
        datetime.strptime(text, "%Y-%m")
    except ValueError as exc:
        raise LedgerError(f"{field}必须是 YYYY-MM 格式，收到的是「{value}」") from exc
    return text


def _number(value: Any, field: str, *, non_negative: bool = True) -> Decimal:
    if value is None or str(value).strip() == "":
        return Decimal("0")
    try:
        num = Decimal(str(value).strip())
    except Exception as exc:
        raise LedgerError(f"{field}必须是数字，收到的是「{value}」") from exc
    if non_negative and num < 0:
        raise LedgerError(f"{field}不允许为负数：{value}")
    return num


def _q(num: Decimal) -> float:
    return float(num.quantize(QTY, rounding=ROUND_HALF_UP))


def _cash(num: Decimal) -> float:
    return float(num.quantize(CASH, rounding=ROUND_HALF_UP))


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


class CarbonLedgerService:
    BASELINE_RULE = {
        "规则编号": "RULE-0001",
        "规则版本": "v2024",
        "生效月份": "2000-01",
        "绿证系数": 1.0,            # 1 MWh 绿色电力核发 1 个绿证
        "减排因子": 0.5703,         # tCO2e/MWh，全国电网平均排放因子口径
        "核发口径说明": "按运行月报发电量口径核发，1个绿证对应1MWh",
    }

    @property
    def store(self):
        # 动态取模块单例：测试或运维切换账本文件后，路由无需重启即可指向同一份数据
        return ledger_store_module.ledger_store

    def __init__(self) -> None:
        self.ensure_baseline_rule()

    # ---- 规则 --------------------------------------------------------------

    def ensure_baseline_rule(self) -> None:
        """首次建账时落一条基线规则；已有规则则不动，避免重启改动已签发口径。"""
        tables = self.store.snapshot()
        if tables["rules"]:
            return
        rule = {"id": 1, **self.BASELINE_RULE, "登记时间": _now()}
        tables["rules"].append(rule)
        self.store.commit(tables)

    def list_rules(self) -> list[dict[str, Any]]:
        return sorted(self.store.snapshot()["rules"], key=lambda r: r["生效月份"])

    def effective_rule(self, tables: dict[str, list[dict[str, Any]]], month: str) -> dict[str, Any]:
        """取核发月份当时生效的规则版本（规则调整后旧月份仍走旧规则）。"""
        candidates = [r for r in tables["rules"] if r["生效月份"] <= month]
        if not candidates:
            raise LedgerError(f"{month} 没有生效中的核发规则，请先登记规则版本")
        return max(candidates, key=lambda r: r["生效月份"])

    def add_rule(self, values: dict[str, Any]) -> dict[str, Any]:
        """调整核发规则 = 新增一个版本；绝不覆盖历史版本、不回算已核发月份。"""
        effective = _month(values.get("生效月份"), "生效月份")
        factor = _number(values.get("绿证系数", 1), "绿证系数")
        if factor == 0:
            raise LedgerError("绿证系数不能为 0")
        carbon = _number(values.get("减排因子"), "减排因子")
        version = str(values.get("规则版本") or "").strip()
        if not version:
            raise LedgerError("规则版本必填，例如 v2026")
        tables = self.store.snapshot()
        if any(r["规则版本"] == version for r in tables["rules"]):
            raise LedgerError(f"规则版本 {version} 已存在，调整规则请使用新版本号")
        if any(r["生效月份"] == effective for r in tables["rules"]):
            raise LedgerError(f"{effective} 已登记过规则，一个生效月份只能对应一个版本")
        rule = {
            "id": _next_id(tables["rules"]),
            "规则编号": f"RULE-{_next_id(tables['rules']):04d}",
            "规则版本": version,
            "生效月份": effective,
            "绿证系数": _q(factor),
            "减排因子": _q(carbon),
            "核发口径说明": str(values.get("核发口径说明") or "按运行月报发电量口径核发"),
            "登记时间": _now(),
        }
        tables["rules"].append(rule)
        self.store.commit(tables)
        return rule

    # ---- 核发 / 回填 --------------------------------------------------------

    def _build_issuance(
        self,
        tables: dict[str, list[dict[str, Any]]],
        *,
        month: str,
        generation: Decimal,
        report_no: str,
        source: str,
    ) -> dict[str, Any]:
        if any(row["核发月份"] == month for row in tables["issuance"]):
            raise LedgerError(f"{month} 已核发过绿证，已核发月份不重算、不重复核发")
        rule = self.effective_rule(tables, month)
        factor = Decimal(str(rule["绿证系数"]))
        carbon_factor = Decimal(str(rule["减排因子"]))
        next_id = _next_id(tables["issuance"])
        return {
            "id": next_id,
            "核发编号": f"GEC-{month.replace('-', '')}-{next_id:02d}",
            "核发月份": month,
            "月报编号": report_no,
            "发电量(MWh)": _q(generation),
            "绿证核发量(个)": _q(generation * factor),
            "碳减排量(tCO2e)": _q(generation * carbon_factor),
            "规则版本": rule["规则版本"],
            "绿证系数": rule["绿证系数"],
            "减排因子": rule["减排因子"],
            "来源": source,
            "登记时间": _now(),
        }

    def backfill(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """存量绿证按核发月份回填：逐月匹配当时生效的规则，整批一次事务写入。

        任何一个月份不合法（月份重复、已核发等），整批失败、一条都不落库。
        """
        if not items:
            raise LedgerError("回填清单为空")
        with self.store.lock:
            tables = self.store.snapshot()
            new_rows: list[dict[str, Any]] = []
            months: list[str] = []
            for raw in items:
                month = _month(raw.get("核发月份"), "核发月份")
                generation = _number(raw.get("发电量(MWh)"), f"{month}发电量")
                if generation == 0:
                    raise LedgerError(f"{month} 发电量为 0，无法回填核发")
                if month in months:
                    raise LedgerError(f"回填清单里 {month} 出现了两次")
                months.append(month)
                report_no = str(raw.get("月报编号") or "").strip() or "存量回填"
                new_rows.append(
                    self._build_issuance(
                        tables, month=month, generation=generation,
                        report_no=report_no, source="存量回填",
                    )
                )
            tables["issuance"].extend(new_rows)
            # 回填也要与存量结算对平：不平整批失败
            self._verify_balances(tables)
            self.store.commit(tables)
        return {"回填月份数": len(new_rows), "核发编号": [row["核发编号"] for row in new_rows]}

    # ---- 结算登记（凭证号幂等） ----------------------------------------------

    def register_settlement(self, values: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """登记一笔交易结算。凭证号全局唯一，重复登记只保留最初那条。"""
        voucher = str(values.get("凭证号") or "").strip()
        if not voucher:
            raise LedgerError("凭证号必填")
        month = _month(values.get("归属月份"), "归属月份")
        certs = _number(values.get("绿证交割量(个)", values.get("绿证交割量")), "绿证交割量")
        carbon = _number(
            values.get("碳减排交割量(t)", values.get("碳减排交割量")), "碳减排交割量"
        )
        amount = _number(values.get("结算金额(元)", values.get("结算金额")), "结算金额")
        if certs == 0 and carbon == 0:
            raise LedgerError("绿证交割量与碳减排交割量不能同时为 0")
        with self.store.lock:
            tables = self.store.snapshot()
            if any(s["月份"] == month for s in tables["months"]):
                # 关账检查必须先于凭证查重：关账后的并发请求一律拒绝，
                # 不能因为凭证恰好相同就被静默当成"去重成功"
                raise LedgerError(f"{month} 已月末关账，不能再补登记结算")
            existing = self._find_voucher(tables, voucher)
            if existing is not None:
                # 并发或重复登记：保留最初那条，结余由流水推导，绝不会被叠加
                return existing, f"凭证号 {voucher} 已登记，已保留 {existing['登记时间']} 的最初记录"
            row = {
                "id": _next_id(tables["settlement"]),
                "凭证号": voucher,
                "归属月份": month,
                "交易日期": str(values.get("交易日期") or "").strip() or month,
                "对手方": str(values.get("对手方") or "").strip(),
                "绿证交割量(个)": _q(certs),
                "碳减排交割量(t)": _q(carbon),
                "结算金额(元)": _cash(amount),
                "登记时间": _now(),
            }
            tables["settlement"].append(row)
            self._verify_balances(tables)
            self.store.commit(tables)
            return row, f"结算 {voucher} 已登记"

    @staticmethod
    def _find_voucher(tables: dict[str, list[dict[str, Any]]], voucher: str) -> dict[str, Any] | None:
        for row in tables["settlement"]:
            if row["凭证号"] == voucher:
                return row
        return None

    # ---- 月末事务：核发 + 结算一次写入、不平整批失败 ---------------------------

    def month_end_close(
        self,
        *,
        month: str,
        report_no: str,
        generation_mwh: Any,
        settlements: list[dict[str, Any]] | None = None,
        finance_balance: Any = None,
    ) -> dict[str, Any]:
        month = _month(month)
        generation = _number(generation_mwh, "发电量(MWh)")
        if generation == 0:
            raise LedgerError(f"{month} 发电量为 0，无法核发")
        with self.store.lock:
            # 全程只在副本上试算；任何一步抛异常，副本直接丢弃，库里什么都没发生
            tables = self.store.snapshot()
            if any(row["核发月份"] == month for row in tables["issuance"]):
                raise LedgerError(f"{month} 已核发过，已核发月份沿用原算法，不重复核发")
            if any(s["月份"] == month for s in tables["months"]):
                raise LedgerError(f"{month} 已关账")
            # 关账当月的结算必须全部进本次批次：预登记过的凭证号要在明细里带上，
            # 不允许关账事务之外还残留当月结算（否则两边口径会被悄悄改口径）
            pre_registered = [
                s for s in tables["settlement"] if s["归属月份"] == month
            ]
            batch_vouchers = [str(raw.get("凭证号") or "").strip() for raw in settlements or []]
            missing = [s["凭证号"] for s in pre_registered if s["凭证号"] not in batch_vouchers]
            if missing:
                raise LedgerError(
                    f"{month} 已有 {len(missing)} 笔预登记结算未纳入本次关账"
                    f"（如 {'、'.join(missing[:3])}），整批失败"
                )

            issuance = self._build_issuance(
                tables, month=month, generation=generation,
                report_no=str(report_no or "").strip() or "运行月报",
                source="月末核发",
            )
            tables["issuance"].append(issuance)

            accepted: list[dict[str, Any]] = []
            duplicates: list[str] = []
            for raw in settlements or []:
                voucher = str(raw.get("凭证号") or "").strip()
                if not voucher:
                    raise LedgerError("结算明细存在缺少凭证号的记录，整批失败")
                known = self._find_voucher(tables, voucher)
                if known is not None:
                    # 库内（本批前已预登记）或本批更早出现的凭证号：只保留最初那条
                    duplicates.append(voucher)
                    if known["归属月份"] != month:
                        raise LedgerError(
                            f"凭证号 {voucher} 已登记在 {known['归属月份']}，不能重复挂到 {month}"
                        )
                    continue
                certs = _number(
                    raw.get("绿证交割量(个)", raw.get("绿证交割量")),
                    f"{voucher}绿证交割量",
                )
                carbon = _number(
                    raw.get("碳减排交割量(t)", raw.get("碳减排交割量")),
                    f"{voucher}碳减排交割量",
                )
                amount = _number(
                    raw.get("结算金额(元)", raw.get("结算金额")), f"{voucher}结算金额"
                )
                if certs == 0 and carbon == 0:
                    raise LedgerError(f"{voucher} 绿证与碳减排交割量同时为 0，整批失败")
                row = {
                    "id": _next_id(tables["settlement"]),
                    "凭证号": voucher,
                    "归属月份": month,
                    "交易日期": str(raw.get("交易日期") or "").strip() or month,
                    "对手方": str(raw.get("对手方") or "").strip(),
                    "绿证交割量(个)": _q(certs),
                    "碳减排交割量(t)": _q(carbon),
                    "结算金额(元)": _cash(amount),
                    "登记时间": _now(),
                }
                tables["settlement"].append(row)
                accepted.append(row)

            balances = self._balances_until(tables, month)
            if balances["结余绿证"] < 0:
                raise LedgerError(
                    f"对不平：{month} 累计交割 {balances['累计交割绿证']} 个，"
                    f"超过累计核发 {balances['累计核发绿证']} 个"
                )
            if finance_balance is not None and str(finance_balance).strip() != "":
                reported = _number(finance_balance, "财务填报结余")
                if reported != Decimal(str(balances["结余绿证"])):
                    raise LedgerError(
                        f"两边对不上：台账结余 {balances['结余绿证']} 个，"
                        f"财务填报 {_q(reported)} 个，整批写入失败、已全部回滚"
                    )
            snapshot = {
                "id": _next_id(tables["months"]),
                "月份": month,
                "当月核发绿证": issuance["绿证核发量(个)"],
                "累计核发绿证": balances["累计核发绿证"],
                "当月交割绿证": balances["当月交割绿证"],
                "累计交割绿证": balances["累计交割绿证"],
                "结余绿证": balances["结余绿证"],
                "当月核发碳减排": issuance["碳减排量(tCO2e)"],
                "累计碳减排": balances["累计核发碳减排"],
                "累计碳减排交割": balances["累计交割碳减排"],
                "结余碳减排": balances["结余碳减排"],
                "财务填报结余": _q(_number(finance_balance, "财务填报结余"))
                if str(finance_balance or "").strip()
                else None,
                "对平状态": "已对平",
                "核对时间": _now(),
            }
            tables["months"].append(snapshot)
            self.store.commit(tables)
        return {"核发": issuance, "结算": accepted, "重复凭证": duplicates, "结余": snapshot}

    @staticmethod
    def _balances_until(tables: dict[str, list[dict[str, Any]]], month: str) -> dict[str, Any]:
        """从流水逐笔推导到指定月份的累计与结余——这是台账唯一的结余算法。"""
        issued = [r for r in tables["issuance"] if r["核发月份"] <= month]
        delivered = [r for r in tables["settlement"] if r["归属月份"] <= month]
        issued_cert = sum((Decimal(str(r["绿证核发量(个)"])) for r in issued), Decimal(0))
        delivered_cert = sum((Decimal(str(r["绿证交割量(个)"])) for r in delivered), Decimal(0))
        issued_carbon = sum((Decimal(str(r["碳减排量(tCO2e)"])) for r in issued), Decimal(0))
        delivered_carbon = sum((Decimal(str(r["碳减排交割量(t)"])) for r in delivered), Decimal(0))
        month_cert = sum(
            (Decimal(str(r["绿证交割量(个)"])) for r in delivered if r["归属月份"] == month),
            Decimal(0),
        )
        return {
            "累计核发绿证": _q(issued_cert),
            "累计交割绿证": _q(delivered_cert),
            "当月交割绿证": _q(month_cert),
            "结余绿证": _q(issued_cert - delivered_cert),
            "累计核发碳减排": _q(issued_carbon),
            "累计交割碳减排": _q(delivered_carbon),
            "结余碳减排": _q(issued_carbon - delivered_carbon),
        }

    def _verify_balances(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        """登记/回填后核对：已关账月份的快照应与流水推导值完全一致。"""
        for snap in tables["months"]:
            actual = self._balances_until(tables, snap["月份"])
            if actual["结余绿证"] != snap["结余绿证"]:
                raise LedgerError(
                    f"台账口径被破坏：{snap['月份']} 快照结余 {snap['结余绿证']}，"
                    f"流水推导 {actual['结余绿证']}"
                )

    # ---- 统一查询：列表 / 分页 / 导出共用这一个口径 ----------------------------

    def query_entries(
        self,
        *,
        month: str | None = None,
        entry_type: str | None = None,
        keyword: str | None = None,
    ) -> list[dict[str, Any]]:
        tables = self.store.snapshot()
        rows: list[dict[str, Any]] = []
        for r in tables["issuance"]:
            rows.append({
                "台账编号": f"IS-{r['id']:04d}",
                "类型": "核发",
                "归属月份": r["核发月份"],
                "凭证号/核发编号": r["核发编号"],
                "月报编号": r["月报编号"],
                "对手方": "",
                "绿证数量(个)": r["绿证核发量(个)"],
                "碳减排量(t)": r["碳减排量(tCO2e)"],
                "结算金额(元)": None,
                "规则版本": f"{r['规则版本']}（系数{r['绿证系数']}/因子{r['减排因子']}）",
                "登记时间": r["登记时间"],
            })
        for r in tables["settlement"]:
            rows.append({
                "台账编号": f"ST-{r['id']:04d}",
                "类型": "结算",
                "归属月份": r["归属月份"],
                "凭证号/核发编号": r["凭证号"],
                "月报编号": "",
                "对手方": r["对手方"],
                "绿证数量(个)": -float(Decimal(str(r["绿证交割量(个)"]))),
                "碳减排量(t)": -float(Decimal(str(r["碳减排交割量(t)"]))),
                "结算金额(元)": r["结算金额(元)"],
                "规则版本": "",
                "登记时间": r["登记时间"],
            })
        if month:
            rows = [r for r in rows if r["归属月份"] == month]
        if entry_type:
            rows = [r for r in rows if r["类型"] == entry_type]
        if keyword:
            key = keyword.strip()
            rows = [
                r for r in rows
                if key in r["凭证号/核发编号"] or key in str(r["月报编号"]) or key in str(r["对手方"])
            ]
        # 确定的排序是"同一份口径"的前提：月份、类型（核发先于结算）、id
        type_rank = {"核发": 0, "结算": 1}
        rows.sort(key=lambda r: (r["归属月份"], type_rank[r["类型"]], r["台账编号"]))
        return rows

    @staticmethod
    def caliber_fingerprint(rows: list[dict[str, Any]]) -> str:
        """全量行的口径指纹：分页包和全量包对的是同一份数据时指纹必然相同。"""
        canonical = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    # ---- 汇总与对账文件 ------------------------------------------------------

    def summary(self) -> dict[str, Any]:
        tables = self.store.snapshot()
        latest_month = max(
            [r["核发月份"] for r in tables["issuance"]]
            + [r["归属月份"] for r in tables["settlement"]],
            default=None,
        )
        balances = (
            self._balances_until(tables, latest_month)
            if latest_month
            else {"累计核发绿证": 0.0, "累计交割绿证": 0.0, "当月交割绿证": 0.0,
                  "结余绿证": 0.0, "累计核发碳减排": 0.0, "累计交割碳减排": 0.0,
                  "结余碳减排": 0.0}
        )
        months = sorted(tables["months"], key=lambda s: s["月份"])
        return {
            "口径说明": "结余=累计核发-累计交割，按流水逐笔推导；冲突时以台账口径为准",
            "结余": balances,
            "月末快照": months,
            "核发笔数": len(tables["issuance"]),
            "结算笔数": len(tables["settlement"]),
        }

    def reconciliation_pack(
        self,
        *,
        month: str | None = None,
        entry_type: str | None = None,
        keyword: str | None = None,
    ) -> dict[str, Any]:
        """对账文件内容：与列表/分页完全同源（query_entries），再附结余与口径指纹。"""
        rows = self.query_entries(month=month, entry_type=entry_type, keyword=keyword)
        return {
            "文件名": f"绿证碳资产对账_{month or '全量'}_{_now().replace(':', '').replace(' ', '_')}",
            "口径": "按运行月报发电量核发；结余由台账流水逐笔推导，冲突时以台账为准",
            "筛选条件": {"月份": month, "类型": entry_type, "关键字": keyword},
            "总条数": len(rows),
            "口径指纹": self.caliber_fingerprint(rows),
            "结余": self.summary()["结余"],
            "明细": rows,
        }


carbon_ledger_service = CarbonLedgerService()
