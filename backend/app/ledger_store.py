"""绿证碳资产台账的持久化存储。

与业务示例数据使用的内存 Store 不同，台账是"账本"：重新进入系统看到的必须是
同一份口径，因此独立落到 data/ledger.json。所有写入都在进程锁内完成，并用
"写临时文件 + 原子替换"的方式落盘，避免导出文件读到写了一半的 JSON。
"""
from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from typing import Any

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_DATA_FILE = os.path.join(_DATA_DIR, "ledger.json")

# 台账的四张表：
# rules        核发规则（按生效月份版本化，调整规则只新增、不覆盖旧版本）
# issuance     核发记录（按核发月份唯一，签发时快照所用规则版本与系数）
# settlement   交易结算流水（凭证号唯一，重复登记只保留最初一条）
# months       月末结余快照（一次事务里核发与结算写完后的对平结果）
_EMPTY_LEDGER: dict[str, list[dict[str, Any]]] = {
    "rules": [],
    "issuance": [],
    "settlement": [],
    "months": [],
}


class LedgerStore:
    def __init__(self, path: str = _DATA_FILE) -> None:
        self._path = path
        self._lock = threading.RLock()
        self._tables = deepcopy(_EMPTY_LEDGER)
        self._loaded = False

    # ---- 读 ----------------------------------------------------------------

    def load(self) -> None:
        with self._lock:
            data: dict[str, Any] = {}
            if os.path.exists(self._path):
                try:
                    with open(self._path, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                except (json.JSONDecodeError, OSError):
                    data = {}
            tables = deepcopy(_EMPTY_LEDGER)
            for name in tables:
                if isinstance(data.get(name), list):
                    tables[name] = data[name]
            self._tables = tables
            self._loaded = True

    def snapshot(self) -> dict[str, list[dict[str, Any]]]:
        """返回台账数据的深拷贝，调用方在副本上试算，写不写得回由 commit 决定。"""
        with self._lock:
            return deepcopy(self._tables)

    # ---- 写 ----------------------------------------------------------------

    def commit(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        """整批替换台账：事务试算成功后一次性落盘，失败的批次不会留下半条数据。"""
        with self._lock:
            clean = deepcopy(_EMPTY_LEDGER)
            for name in clean:
                clean[name] = list(tables.get(name, []))
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            tmp_path = f"{self._path}.tmp"
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(clean, fh, ensure_ascii=False, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_path, self._path)
            self._tables = clean

    def reset(self, tables: dict[str, list[dict[str, Any]]] | None = None) -> None:
        """仅供测试或初始化：把台账恢复为给定状态（默认空账）。"""
        base = deepcopy(_EMPTY_LEDGER)
        if tables:
            for name in base:
                base[name] = list(tables.get(name, []))
        self.commit(base)

    @property
    def lock(self) -> threading.RLock:
        """事务期间持有的锁，保证并发登记按凭证号去重、结余不被叠加。"""
        return self._lock


ledger_store = LedgerStore()
ledger_store.load()
