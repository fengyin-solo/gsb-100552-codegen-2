"""接口出入参模型：列表分页、动作结果与各模块的明细结构。"""
from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class PageResult(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    size: int = 20
    # 全量结果的口径指纹（指纹基于当前过滤条件下的全量行，与分页无关），
    # 与对账文件里的口径指纹一致时，即证明列表、全量打包、分页打包同源。
    caliber: str | None = None


class ActionResult(BaseModel):
    ok: bool
    message: str
    entry: dict[str, Any] | None = None


class EntryPayload(BaseModel):
    """登记或修改一条业务记录时提交的字段集合。"""

    values: dict[str, Any] = Field(default_factory=dict)
    remark: str | None = None



class PvArrayEntry(BaseModel):
    """光伏阵列明细结构。"""

    field_0: str | None = None  # 阵列编号
    field_1: str | None = None  # 所属片区
    field_2: str | None = None  # 组件型号
    field_3: str | None = None  # 单块功率
    field_4: str | None = None  # 串联片数
    field_5: str | None = None  # 总装机容量
    field_6: str | None = None  # 投运日期
    field_7: str | None = None  # 阵列状态

class InverterEntry(BaseModel):
    """逆变器明细结构。"""

    field_0: str | None = None  # 逆变器编号
    field_1: str | None = None  # 品牌型号
    field_2: str | None = None  # 额定功率
    field_3: str | None = None  # 输入电压范围
    field_4: str | None = None  # 所属阵列
    field_5: str | None = None  # 运行温度
    field_6: str | None = None  # 日均发电量
    field_7: str | None = None  # 运行状态

class CombinerBoxEntry(BaseModel):
    """汇流箱明细结构。"""

    field_0: str | None = None  # 汇流箱编号
    field_1: str | None = None  # 所属阵列
    field_2: str | None = None  # 输入路数
    field_3: str | None = None  # 熔断器状态
    field_4: str | None = None  # 防雷模块状态
    field_5: str | None = None  # 通讯状态
    field_6: str | None = None  # 箱体温度
    field_7: str | None = None  # 运行状态

class TransformerEntry(BaseModel):
    """变压器明细结构。"""

    field_0: str | None = None  # 变压器编号
    field_1: str | None = None  # 电压等级
    field_2: str | None = None  # 额定容量
    field_3: str | None = None  # 油温上限
    field_4: str | None = None  # 绕组温度
    field_5: str | None = None  # 油位状态
    field_6: str | None = None  # 瓦斯保护状态
    field_7: str | None = None  # 运行状态

class EnergyStorageEntry(BaseModel):
    """储能电池组明细结构。"""

    field_0: str | None = None  # 电池组编号
    field_1: str | None = None  # 电池类型
    field_2: str | None = None  # 额定容量
    field_3: str | None = None  # SOC上限
    field_4: str | None = None  # 充放电循环
    field_5: str | None = None  # 电池温度
    field_6: str | None = None  # 内阻变化率
    field_7: str | None = None  # 运行状态

class BoostingStationEntry(BaseModel):
    """升压站明细结构。"""

    field_0: str | None = None  # 升压站编号
    field_1: str | None = None  # 进线电压
    field_2: str | None = None  # 出线电压
    field_3: str | None = None  # 主变容量
    field_4: str | None = None  # 母线状态
    field_5: str | None = None  # 断路器状态
    field_6: str | None = None  # 无功补偿
    field_7: str | None = None  # 运行状态

class MeterEntry(BaseModel):
    """关口表计明细结构。"""

    field_0: str | None = None  # 表计编号
    field_1: str | None = None  # 计量点名称
    field_2: str | None = None  # 表计精度
    field_3: str | None = None  # 正向有功电量
    field_4: str | None = None  # 反向有功电量
    field_5: str | None = None  # 上月示数
    field_6: str | None = None  # 本月示数
    field_7: str | None = None  # 通讯状态

class EnvironmentEntry(BaseModel):
    """环境监测站明细结构。"""

    field_0: str | None = None  # 站点编号
    field_1: str | None = None  # 安装位置
    field_2: str | None = None  # 辐照度
    field_3: str | None = None  # 环境温度
    field_4: str | None = None  # 风速
    field_5: str | None = None  # 风向
    field_6: str | None = None  # 积灰比
    field_7: str | None = None  # 通讯状态

class CleaningEntry(BaseModel):
    """清洗任务明细结构。"""

    field_0: str | None = None  # 任务编号
    field_1: str | None = None  # 清洗区域
    field_2: str | None = None  # 清洗方式
    field_3: str | None = None  # 计划日期
    field_4: str | None = None  # 作业人员
    field_5: str | None = None  # 用水吨数
    field_6: str | None = None  # 清洗后PR值
    field_7: str | None = None  # 清洗状态

class PatrolEntry(BaseModel):
    """巡视记录明细结构。"""

    field_0: str | None = None  # 记录编号
    field_1: str | None = None  # 巡视区域
    field_2: str | None = None  # 巡视日期
    field_3: str | None = None  # 巡视人员
    field_4: str | None = None  # 发现缺陷数
    field_5: str | None = None  # 红外测温结果
    field_6: str | None = None  # 接线端子温度
    field_7: str | None = None  # 巡视状态

class DefectEntry(BaseModel):
    """设备缺陷明细结构。"""

    field_0: str | None = None  # 缺陷编号
    field_1: str | None = None  # 发现日期
    field_2: str | None = None  # 缺陷设备
    field_3: str | None = None  # 缺陷类别
    field_4: str | None = None  # 严重等级
    field_5: str | None = None  # 处理方案
    field_6: str | None = None  # 整改时限
    field_7: str | None = None  # 缺陷状态

class MaintenanceEntry(BaseModel):
    """检修计划明细结构。"""

    field_0: str | None = None  # 计划编号
    field_1: str | None = None  # 检修设备
    field_2: str | None = None  # 检修类别
    field_3: str | None = None  # 计划开始
    field_4: str | None = None  # 计划结束
    field_5: str | None = None  # 责任人
    field_6: str | None = None  # 安全措施
    field_7: str | None = None  # 计划状态

class SparePartsEntry(BaseModel):
    """备件物料明细结构。"""

    field_0: str | None = None  # 备件编号
    field_1: str | None = None  # 备件名称
    field_2: str | None = None  # 规格型号
    field_3: str | None = None  # 适用设备
    field_4: str | None = None  # 安全存量
    field_5: str | None = None  # 当前存量
    field_6: str | None = None  # 存放位置
    field_7: str | None = None  # 备件状态

class AlarmEntry(BaseModel):
    """告警事件明细结构。"""

    field_0: str | None = None  # 告警编号
    field_1: str | None = None  # 告警来源
    field_2: str | None = None  # 告警类型
    field_3: str | None = None  # 触发时间
    field_4: str | None = None  # 告警阈值
    field_5: str | None = None  # 当前值
    field_6: str | None = None  # 确认人
    field_7: str | None = None  # 告警状态

class DispatchEntry(BaseModel):
    """调度指令单明细结构。"""

    field_0: str | None = None  # 指令编号
    field_1: str | None = None  # 下发单位
    field_2: str | None = None  # 指令类型
    field_3: str | None = None  # 下发时间
    field_4: str | None = None  # 执行时限
    field_5: str | None = None  # 执行人
    field_6: str | None = None  # 执行结果
    field_7: str | None = None  # 指令状态

class SafetyEntry(BaseModel):
    """安全措施票明细结构。"""

    field_0: str | None = None  # 措施编号
    field_1: str | None = None  # 措施类型
    field_2: str | None = None  # 涉及设备
    field_3: str | None = None  # 签发人
    field_4: str | None = None  # 执行人
    field_5: str | None = None  # 监护人
    field_6: str | None = None  # 有效期至
    field_7: str | None = None  # 措施状态

class ContractEntry(BaseModel):
    """运维合同明细结构。"""

    field_0: str | None = None  # 合同编号
    field_1: str | None = None  # 合同名称
    field_2: str | None = None  # 签约甲方
    field_3: str | None = None  # 签约乙方
    field_4: str | None = None  # 合同金额
    field_5: str | None = None  # 起止日期
    field_6: str | None = None  # 续签条款
    field_7: str | None = None  # 合同状态

class ReportEntry(BaseModel):
    """运行月报明细结构。"""

    field_0: str | None = None  # 月报编号
    field_1: str | None = None  # 统计月份
    field_2: str | None = None  # 发电量
    field_3: str | None = None  # 等效利用小时
    field_4: str | None = None  # 综合效率PR
    field_5: str | None = None  # 设备可利用率
    field_6: str | None = None  # 故障停机时间
    field_7: str | None = None  # 月报状态


class RulePayload(BaseModel):
    """核发规则版本登记。"""

    规则版本: str
    生效月份: str
    绿证系数: float = 1.0
    减排因子: float = 0.5703
    核发口径说明: str | None = None


class BackfillPayload(BaseModel):
    """存量绿证按核发月份回填，整批一次写入。"""

    items: list[dict[str, Any]] = Field(default_factory=list)


class SettlementPayload(BaseModel):
    """单笔交易结算登记（凭证号去重）。"""

    凭证号: str
    归属月份: str
    交易日期: str | None = None
    对手方: str | None = None
    绿证交割量: float = 0
    碳减排交割量: float = 0
    结算金额: float = 0


class SettlementItem(BaseModel):
    """月末事务里的单笔结算明细。"""

    凭证号: str
    交易日期: str | None = None
    对手方: str | None = None
    绿证交割量: float = 0
    碳减排交割量: float = 0
    结算金额: float = 0


class MonthEndPayload(BaseModel):
    """月末核发与结算：一次事务里一起写入，不平整批失败。"""

    月份: str
    月报编号: str
    发电量MWh: float
    结算明细: list[SettlementItem] = Field(default_factory=list)
    财务填报结余: float | None = None
