"""时序测点领域服务：IoT测点定义、采集管理和数据模拟。

提供测点配置、模拟数据生成、批量采集管理等真实业务能力。
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.services.ts_reconciliation import reconciliation_service
from app.store import store

CST = timezone(timedelta(hours=8))
random.seed(42)


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def _now_dt() -> datetime:
    return datetime.now(CST)


class TsPointService(BaseModuleService):
    MODULE = "ts_point"
    ENTITY = "测点定义"
    OPERATION_TERM = "采集"
    LIST_FIELDS = ["测点编号", "设备编号", "测点名称", "数据类型", "采集间隔", "上报方式", "单位符号", "量程范围"]
    REQUIRED_FIELDS = ["测点编号", "设备编号", "测点名称"]
    STATUS_ORDER = ["待配置", "采集中", "已停采", "已废弃"]
    ACTION_RULES = {"启动采集": "采集中", "停止采集": "已停采", "标记废弃": "已废弃"}
    NEGATIVE_ACTIONS = []

    def simulate_data(self, point_id: str, count: int = 10) -> dict[str, Any]:
        """模拟测点数据采集：根据测点配置生成模拟时序数据。

        业务流程：
        1. 查找测点定义
        2. 根据数据类型和量程范围生成模拟值
        3. 按采集间隔生成时间序列
        """
        rows = store.rows(self.MODULE)
        point = None
        for row in rows:
            if row.get("测点编号") == point_id:
                point = row
                break
        if point is None:
            return {"ok": False, "message": f"测点 {point_id} 不存在"}

        data_type = point.get("数据类型", "float64")
        range_str = point.get("量程范围", "0-100")
        try:
            parts = range_str.split("-")
            low = float(parts[0])
            high = float(parts[-1])
        except (ValueError, TypeError):
            low, high = 0, 100

        try:
            interval = int(point.get("采集间隔", "10").replace("s", ""))
        except (ValueError, TypeError):
            interval = 10

        # 生成模拟数据
        base = random.uniform(low, high)
        data_points = []
        now = _now_dt()
        for i in range(count):
            ts = (now - timedelta(seconds=interval * (count - i))).isoformat(timespec="seconds")
            # 叠加正弦波动模拟真实数据
            noise = random.uniform(-0.05, 0.05) * (high - low)
            wave = math.sin(i * 0.5) * (high - low) * 0.1
            if data_type in ("int32", "int64"):
                value = int(base + wave + noise)
            else:
                value = round(base + wave + noise, 3)
            data_points.append({"timestamp": ts, "value": value, "unit": point.get("单位符号", "")})

        return {
            "ok": True,
            "message": f"测点 {point_id} 模拟生成 {count} 条数据",
            "point_id": point_id,
            "device_id": point.get("设备编号"),
            "data_type": data_type,
            "interval_seconds": interval,
            "data": data_points,
        }

    def batch_collect_status(self) -> dict[str, Any]:
        """批量采集状态：统计各设备测点的采集情况。"""
        rows = store.rows(self.MODULE)
        by_device = {}
        by_status = {}
        for row in rows:
            dev = row.get("设备编号", "未知")
            if dev not in by_device:
                by_device[dev] = {"total": 0, "collecting": 0, "stopped": 0}
            by_device[dev]["total"] += 1
            if row.get("status") == "采集中":
                by_device[dev]["collecting"] += 1
            elif row.get("status") == "已停采":
                by_device[dev]["stopped"] += 1

            s = row.get("status", "未知")
            by_status[s] = by_status.get(s, 0) + 1

        return {
            "total_points": len(rows),
            "by_status": by_status,
            "by_device": [{"设备编号": k, **v} for k, v in by_device.items()],
            "collecting_rate": round(
                by_status.get("采集中", 0) / max(len(rows), 1) * 100, 1
            ),
        }

    # ------------------------------------------------------------------
    # 同位冲突、争议处理单与窗口裁决
    #
    # HTTP 层和潜在的服务级测试可以直接调用这些语义化方法；真正的状态
    # 与规则集中在 TsReconciliationService，避免路由层编写业务判断。
    # ------------------------------------------------------------------
    def reconciliation_snapshot(self) -> dict[str, Any]:
        return reconciliation_service.snapshot()

    def reset_reconciliation(self, scenario: str = "conflict") -> dict[str, Any]:
        return reconciliation_service.reset(scenario)

    def report_sample(self, point_id: Any = "P", sample_time: Any = "11:00",
                      value: Any = None, source: str = "channel-A", **extra: Any) -> dict[str, Any]:
        # 同时兼容 report_sample({...}) 与 report_sample(point, time, value, source)。
        if isinstance(point_id, dict):
            return reconciliation_service.ingest_from_payload(point_id)
        payload = {
            "point_code": point_id,
            "sample_time": sample_time,
            "value": value,
            "source": source,
            **extra,
        }
        return reconciliation_service.ingest_from_payload(payload)

    # 常见命名别名，便于不同调用方按“读数/上报/接收”语义使用。
    ingest_sample = report_sample
    receive_sample = report_sample
    submit_reading = report_sample
    report_reading = report_sample

    def list_disputes(self) -> list[dict[str, Any]]:
        return reconciliation_service.list_disputes()

    def get_dispute(self, dispute_id: str, *, reopen: bool = False) -> dict[str, Any] | None:
        return reconciliation_service.get_dispute(dispute_id, reopen=reopen)

    def adjudicate_dispute(self, dispute_id: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        # 兼容多种服务级调用：adjudicate(id, 14, "依据")、
        # adjudicate(id, sample_id="...") 或 adjudicate(id, {"adopted_value": 14})。
        payload: dict[str, Any] = dict(kwargs)
        if args:
            first = args[0]
            if isinstance(first, dict):
                payload.update(first)
            elif isinstance(first, str) and (
                first.startswith(("S-", "CAND-", "DSP-")) or first.upper() in {"A", "B"}
            ):
                if first.upper() in {"A", "B"}:
                    payload.setdefault("side", first)
                else:
                    payload.setdefault("sample_id", first)
            else:
                payload.setdefault("value", first)
        if len(args) >= 2:
            payload.setdefault("reason", args[1])
        if len(args) >= 3:
            payload.setdefault("operator", args[2])
        return reconciliation_service.adjudicate_from_payload(dispute_id, payload)

    resolve_dispute = adjudicate_dispute
    decide_dispute = adjudicate_dispute
    rule_on_dispute = adjudicate_dispute

    def read_window(self, window_id: Any = "WIN-P-1100", *args: Any, **kwargs: Any) -> dict[str, Any]:
        if isinstance(window_id, dict):
            return reconciliation_service.read_window_from_payload(window_id)
        payload: dict[str, Any] = dict(kwargs)
        if str(window_id).startswith("WIN"):
            payload["window_id"] = window_id
        else:
            payload["point_code"] = window_id
            if args:
                payload["sample_time"] = args[0]
            if len(args) >= 2:
                payload["value"] = args[1]
        return reconciliation_service.read_window(payload)

    def continue_window(self, window_id: str = "WIN-P-1100", *args: Any, **kwargs: Any) -> dict[str, Any]:
        if args and "sign" not in kwargs:
            kwargs["sign"] = bool(args[0])
        return reconciliation_service.continue_window(window_id, **kwargs)

    def sign_window(self, window_id: str = "WIN-P-1100", **kwargs: Any) -> dict[str, Any]:
        return reconciliation_service.sign_window(window_id, **kwargs)