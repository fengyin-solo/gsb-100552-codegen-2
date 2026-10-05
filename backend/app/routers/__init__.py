"""业务模块路由汇总。

这里统一按别名导入再暴露 ROUTERS：模块名有可能和内置名撞车（某个业务模块就叫 dict、list
这种名字时），按名字直接 import 会把内置类型覆盖掉，函数注解在运行时求值就会报
'module' object is not subscriptable。
"""
from __future__ import annotations

from app.routers import pv_array as router_pv_array
from app.routers import inverter as router_inverter
from app.routers import combiner_box as router_combiner_box
from app.routers import transformer as router_transformer
from app.routers import energy_storage as router_energy_storage
from app.routers import boosting_station as router_boosting_station
from app.routers import meter as router_meter
from app.routers import environment as router_environment
from app.routers import cleaning as router_cleaning
from app.routers import patrol as router_patrol
from app.routers import defect as router_defect
from app.routers import maintenance as router_maintenance
from app.routers import spare_parts as router_spare_parts
from app.routers import alarm as router_alarm
from app.routers import dispatch as router_dispatch
from app.routers import safety as router_safety
from app.routers import contract as router_contract
from app.routers import report as router_report
from app.routers import carbon_ledger as router_carbon_ledger

ROUTERS = [router_pv_array, router_inverter, router_combiner_box, router_transformer, router_energy_storage, router_boosting_station, router_meter, router_environment, router_cleaning, router_patrol, router_defect, router_maintenance, router_spare_parts, router_alarm, router_dispatch, router_safety, router_contract, router_report, router_carbon_ledger]
