"""内存数据仓库：给每个业务模块准备一份可筛选、可流转的示例数据。

真实项目里这里会换成数据库访问层；当前实现只依赖标准库，保证克隆下来就能起。

绿证碳资产台账对一致性要求高（月末核发与结算必须同生共死、凭证号并发去重），
所以这里额外提供两件数据库本来就有的东西：
- transaction()：事务上下文，写入期间任一步抛错就整批回滚到进入前快照；
- 进程内互斥锁：并发的结算登记/关账被串行化，保证「先到先得、后者去重」。
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from copy import deepcopy
from typing import Any

from app.seed import SEED_ROWS


class Store:
    def __init__(self) -> None:
        self._tables: dict[str, list[dict[str, Any]]] = {
            name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()
        }
        self._lock = threading.RLock()

    @property
    def lock(self) -> threading.RLock:
        """业务层在「先查重再写入」这类复合操作上需要持有的同一把锁。"""
        return self._lock

    @contextmanager
    def transaction(self):
        """事务：正常结束提交，抛出异常时把所有表恢复到进入前的快照。

        内存库里没有 undo log，用深拷贝快照实现整批回滚；同把 RLock 保证
        并发事务完全串行，读已提交在这里退化成可串行化，对账更简单。
        """
        with self._lock:
            snapshot = deepcopy(self._tables)
            try:
                yield self
            except BaseException:
                self._tables = snapshot
                raise

    def next_id(self, module: str) -> int:
        """在事务内调用：取该表下一个主键。"""
        return max((int(row.get("id", 0)) for row in self.rows(module)), default=0) + 1

    def module_names(self) -> list[str]:
        return sorted(self._tables)

    def rows(self, module: str) -> list[dict[str, Any]]:
        return self._tables.setdefault(module, [])

    def find(self, module: str, entry_id: int) -> dict[str, Any] | None:
        for row in self.rows(module):
            if int(row.get("id", 0)) == entry_id:
                return row
        return None

    def overview(self) -> dict[str, object]:
        modules: list[dict[str, object]] = []
        for name in self.module_names():
            rows = self.rows(name)
            modules.append({
                "name": name,
                "created": len(rows),
                "pending": sum(1 for row in rows if row.get("pending")),
                "abnormal": sum(1 for row in rows if row.get("abnormal")),
            })
        cards = [
            {"label": "业务模块", "value": len(modules)},
            {"label": "今日新增", "value": sum(int(item["created"]) for item in modules)},
            {"label": "待处理", "value": sum(int(item["pending"]) for item in modules)},
            {"label": "异常量", "value": sum(int(item["abnormal"]) for item in modules)},
        ]
        return {"cards": cards, "modules": modules}


store = Store()
