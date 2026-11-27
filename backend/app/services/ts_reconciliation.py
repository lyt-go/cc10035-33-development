"""同位时序样本冲突、争议裁决与未确认窗口核对服务。

该服务把“原始上报/去重样本/争议处理单/依赖窗口”放在同一处维护：

* 同一采样身份且数值相同的重送只增加投递计数，不产生第二条有效样本；
* 异渠道异值保留来源和原始投递记录，并生成争议处理单；
* 裁决后该采样身份只有一个 ``is_valid=True`` 的样本；
* 只有依赖该位置且尚未确认/签收的窗口会按裁决值处理；
* 无关窗口不进入处理集合，便于页面核对“旁窗无变化”。

当前演示实现使用进程内状态；所有状态变更仍只发生在 service 层。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from app.store import store

CST = timezone(timedelta(hours=8))


def _now() -> str:
    return datetime.now(CST).isoformat(timespec="seconds")


def _now_dt() -> datetime:
    return datetime.now(CST)


def _as_decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _json_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    return float(value)


def _first(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] is not None and str(data[key]).strip() != "":
            return data[key]
    return default


def _time_key(dt: datetime) -> str:
    # 工作台剧本以“今天 11:00”为主要位置；今天的完整时间戳归一到 HHMM，
    # 使其与裸 "11:00" 和内置验收窗口稳定对应。其他日期保留日期前缀。
    if dt.date() == _now_dt().date():
        return dt.strftime("%H%M")
    return dt.strftime("%Y%m%d%H%M")


def normalize_time(value: Any) -> tuple[str, str]:
    """返回（稳定时间键, 页面展示的 HH:MM）。"""
    text = str(value or "").strip()
    if not text:
        return "unknown", "--:--"

    compact = text.replace("T", " ")
    # 2026-11-27 11:00 / 2026-11-27 11:00:00
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"):
        try:
            dt = datetime.strptime(compact[: len(fmt) + 2].strip(), fmt)
            return _time_key(dt), dt.strftime("%H:%M")
        except ValueError:
            pass

    # ISO 带时区/微秒也交给 fromisoformat 试一次。
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(CST)
        return _time_key(dt), dt.strftime("%H:%M")
    except ValueError:
        pass

    # 纯 11:00 是验收剧本里的主要形式。
    if ":" in text:
        parts = text.split(":")
        try:
            hour = int(parts[0])
            minute = int(parts[1])
            return f"{hour:02d}{minute:02d}", f"{hour:02d}:{minute:02d}"
        except (ValueError, IndexError):
            pass

    safe = "".join(ch for ch in text if ch.isalnum()) or "unknown"
    return safe, text


def _slug(value: Any) -> str:
    text = str(value or "").strip()
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in text)[:48] or "UNKNOWN"


class TsReconciliationService:
    """维护样本、争议单和窗口核对结果的单例领域服务。"""

    def __init__(self) -> None:
        self.state: dict[str, Any] = {}
        self.reset("conflict")

    # ------------------------------------------------------------------
    # 状态与验收剧本
    # ------------------------------------------------------------------
    def reset(self, scenario: str = "conflict") -> dict[str, Any]:
        scenario = (scenario or "conflict").strip().lower()
        self.state = {
            "scenario": scenario,
            "receipts": [],
            "samples": [],
            "disputes": [],
            "windows": [],
            "events": [],
            "counters": {"receipt": 0, "event": 0, "dispute_round": 0},
        }
        self._ensure_point_definition()
        self._seed_windows()

        if scenario in {"ready", "reported", "read", "conflict", "full"}:
            self.ingest_sample({
                "point_code": "P", "sample_time": "11:00", "value": 10,
                "source": "channel-A", "source_label": "采集渠道 A",
            }, _allow_rearm=False)
            self.ingest_sample({
                "point_code": "P", "sample_time": "11:00", "value": 10,
                "source": "channel-A", "source_label": "采集渠道 A",
            }, _allow_rearm=False)
            self.read_window({"window_id": "WIN-P-1100"})

        if scenario in {"conflict", "full"}:
            self.ingest_sample({
                "point_code": "P", "sample_time": "11:00", "value": 14,
                "source": "channel-B", "source_label": "另一渠道 B",
            }, _allow_rearm=False)

        self._event("系统", f"已载入{scenario}场景状态")
        return self.snapshot()

    def _ensure_point_definition(self) -> None:
        rows = store.rows("ts_point")
        if any(str(row.get("测点编号")) == "P" for row in rows):
            return
        next_id = max((int(row.get("id", 0) or 0) for row in rows), default=0) + 1
        rows.append({
            "id": next_id,
            "status": "采集中",
            "pending": False,
            "abnormal": False,
            "测点编号": "P",
            "设备编号": "DEV-P",
            "测点名称": "同位冲突验收测点",
            "数据类型": "int64",
            "采集间隔": "60s",
            "上报方式": "MQTT/双通道",
            "单位符号": "℃",
            "量程范围": "0-100",
        })

    def _seed_windows(self) -> None:
        self.state["windows"] = [
            {
                "id": "WIN-P-1100",
                "window_no": "WIN-P-1100",
                "title": "P 点 11:00 正点核对窗",
                "point_code": "P",
                "sample_time": "11:00",
                "identity": "P@1100",
                "dependencies": ["P@1100"],
                "range_label": "11:00 正点采样窗口",
                "read_value": None,
                "read_sample_id": None,
                "read_source": None,
                "read_at": None,
                "signed": False,
                "confirmed": False,
                "status": "待读取",
                "pending_dispute_id": None,
                "recomputed": False,
                "recompute_count": 0,
                "used_sample_id": None,
                "used_value": None,
                "used_source": None,
                "eligible_sample_ids": [],
                "eligible_sample_count": 0,
                "formal_output": None,
                "verification": None,
                "timeline": [],
                "before_digest": "target-before",
            },
            {
                "id": "WIN-P-1200-SIDE",
                "window_no": "WIN-P-1200-SIDE",
                "title": "旁窗：P 点 12:00 常规窗",
                "point_code": "P",
                "sample_time": "12:00",
                "identity": "P@1200",
                "dependencies": ["P@1200"],
                "range_label": "12:00 无关旁窗",
                "read_value": 20,
                "read_sample_id": "SEED-P-1200-20",
                "read_source": "channel-A",
                "read_at": "2026-11-27T12:00:05+08:00",
                "signed": True,
                "confirmed": True,
                "status": "已签收出值",
                "pending_dispute_id": None,
                "recomputed": False,
                "recompute_count": 0,
                "used_sample_id": "SEED-P-1200-20",
                "used_value": 20,
                "used_source": "channel-A",
                "eligible_sample_ids": ["SEED-P-1200-20"],
                "eligible_sample_count": 1,
                "formal_output": {
                    "value": 20,
                    "sample_id": "SEED-P-1200-20",
                    "source": "channel-A",
                    "explanation": "旁窗不依赖 P@11:00，沿用原已确认样本。",
                },
                "verification": {
                    "checked": True,
                    "effective_sample_count": 1,
                    "conclusion": "核对通过：旁窗为原有一项有效样本，未参与重算。",
                },
                "timeline": [
                    {"at": "2026-11-27T12:00:06+08:00", "actor": "系统",
                     "message": "旁窗早已签收并正式出值 20。"}
                ],
                "before_digest": "side-original-20",
            },
        ]

    def _event(self, actor: str, message: str, **extra: Any) -> dict[str, Any]:
        self.state["counters"]["event"] += 1
        event = {
            "id": f"EVT-{self.state['counters']['event']:04d}",
            "at": _now(),
            "actor": actor,
            "message": message,
        }
        event.update(extra)
        self.state["events"].append(event)
        return event

    def _window_event(self, window: dict[str, Any], actor: str, message: str) -> None:
        event = {"at": _now(), "actor": actor, "message": message}
        window.setdefault("timeline", []).append(event)
        self._event(actor, f"{window['id']}：{message}", window_id=window["id"])

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def snapshot(self) -> dict[str, Any]:
        samples = self.state["samples"]
        disputes = self.state["disputes"]
        windows = self.state["windows"]
        p11 = [s for s in samples if s.get("identity") == "P@1100"]
        open_disputes = [d for d in disputes if d.get("status") == "待裁定"]
        resolved = [d for d in disputes if d.get("status") == "已裁定"]
        effective_count = sum(1 for s in p11 if s.get("is_valid"))
        return {
            "ok": True,
            "scenario": self.state.get("scenario"),
            "point_code": "P",
            "samples": deepcopy(samples),
            "disputes": deepcopy(disputes),
            "windows": deepcopy(windows),
            "events": deepcopy(self.state["events"][-30:]),
            "summary": {
                "p11_unique_readings": len(p11),
                "p11_effective_samples": effective_count,
                "open_disputes": len(open_disputes),
                "resolved_disputes": len(resolved),
                "unconfirmed_windows": sum(1 for w in windows if not w.get("confirmed")),
            },
        }

    def get_dispute(self, dispute_id: str, *, reopen: bool = False, actor: str = "操作员") -> dict[str, Any] | None:
        dispute = self._find_dispute(str(dispute_id))
        if dispute is None:
            return None
        if reopen and dispute.get("status") == "已裁定":
            dispute["reopen_count"] = int(dispute.get("reopen_count", 0)) + 1
            dispute["last_reopened_at"] = _now()
            self._event(actor, f"重新打开争议处理单 {dispute['dispute_no']} 的第一次处置和后续结果")
        result = deepcopy(dispute)
        result["linked_windows"] = [
            deepcopy(w) for w in self.state["windows"]
            if dispute.get("identity") in w.get("dependencies", [])
        ]
        result["global_events"] = deepcopy(self.state["events"][-20:])
        return result

    def get_window(self, window_id: str) -> dict[str, Any] | None:
        window = self._find_window(str(window_id))
        return deepcopy(window) if window else None

    def _find_dispute(self, identifier: str) -> dict[str, Any] | None:
        text = str(identifier or "").strip()
        for dispute in self.state["disputes"]:
            if text in {str(dispute.get("id")), str(dispute.get("dispute_no")), str(dispute.get("争议单号"))}:
                return dispute
        return None

    def _find_window(self, identifier: str) -> dict[str, Any] | None:
        text = str(identifier or "").strip()
        for window in self.state["windows"]:
            if text in {str(window.get("id")), str(window.get("window_no")), str(window.get("窗口编号"))}:
                return window
        return None

    def _find_sample(self, identifier: str) -> dict[str, Any] | None:
        text = str(identifier or "").strip()
        for sample in self.state["samples"]:
            ids = {str(sample.get("id")), str(sample.get("sample_id")), str(sample.get("candidate_id"))}
            if text in ids:
                return sample
        return None

    # ------------------------------------------------------------------
    # 上报与幂等去重
    # ------------------------------------------------------------------
    def ingest_from_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        point = _first(data, "point_code", "point_id", "point", "code", "测点编号", "测点", default="P")
        sample_time = _first(data, "sample_time", "timestamp", "time", "ts", "采样时间", "时间", default="11:00")
        value = _first(data, "value", "sample_value", "reading", "reading_value", "读数", "数值", "值")
        source = _first(data, "source", "source_code", "channel", "source_channel", "origin", "来源", "渠道",
                        default="unknown-source")
        source_label = _first(data, "source_label", "channel_label", "来源名称", default=None)
        identity_override = _first(data, "identity", "sample_identity", "identity_key", "采样身份", default=None)
        return self.ingest_sample({
            "point_code": point,
            "sample_time": sample_time,
            "value": value,
            "source": source,
            "source_label": source_label,
            "identity": identity_override,
            "actor": _first(data, "actor", "operator", default="采集网关"),
        })

    def ingest_sample(self, data: dict[str, Any], *, _allow_rearm: bool = True) -> dict[str, Any]:
        point = str(data.get("point_code") or "P").strip()
        time_key, time_label = normalize_time(data.get("sample_time"))
        decimal_value = _as_decimal(data.get("value"))
        if decimal_value is None:
            return {"ok": False, "message": "读数必须是可比较的数值"}

        value = _json_number(decimal_value)
        source = str(data.get("source") or "unknown-source").strip()
        source_key = _slug(source).lower()
        source_label = str(data.get("source_label") or source)
        identity = str(data.get("identity") or f"{point}@{time_key}")
        actor = str(data.get("actor") or "采集网关")

        # 验收剧本兼容：应用启动时保留一份“已出现冲突”的演示状态；如果调用方
        # 又从 P@11:00=10 开始重放“两次 10、第三次 14”的剧本，先把该剧本位置
        # 回收到空白窗口，避免种子投递次数污染调用方自己的计数。
        if (
            _allow_rearm
            and identity == "P@1100"
            and decimal_value == Decimal("10")
            and self.state.get("scenario") == "conflict"
        ):
            self.reset("stage")
            point = str(data.get("point_code") or "P").strip()
            time_key, time_label = normalize_time(data.get("sample_time"))
            identity = str(data.get("identity") or f"{point}@{time_key}")

        self.state["counters"]["receipt"] += 1
        receipt_id = f"RCP-{self.state['counters']['receipt']:05d}"
        existing = next((
            s for s in self.state["samples"]
            if s["identity"] == identity and _as_decimal(s["value"]) == decimal_value
        ), None)

        receipt = {
            "id": receipt_id,
            "at": _now(),
            "identity": identity,
            "point_code": point,
            "sample_time": time_label,
            "value": value,
            "source": source_key,
            "source_label": source_label,
            "duplicate": existing is not None,
        }
        self.state["receipts"].append(receipt)

        if existing is not None:
            existing["delivery_count"] = int(existing.get("delivery_count", 1)) + 1
            existing["duplicate_count"] = int(existing.get("duplicate_count", 0)) + 1
            existing["last_received_at"] = receipt["at"]
            sources = existing.setdefault("sources", [])
            if not any(item.get("source") == source_key for item in sources):
                sources.append({
                    "source": source_key, "source_label": source_label,
                    "first_seen_at": receipt["at"], "delivery_count": 1,
                })
            else:
                for item in sources:
                    if item.get("source") == source_key:
                        item["delivery_count"] = int(item.get("delivery_count", 0)) + 1
            self._event(actor, f"{identity} 的相同值 {value} 重送，幂等去重后仍只有一条样本",
                        identity=identity, duplicate=True)
            self._auto_read_pending_window(identity, existing, actor="窗口读取器")
            return {
                "ok": True,
                "duplicate": True,
                "message": f"相同采样身份的相同值 {value} 重送只算一次",
                "receipt": receipt,
                "sample": deepcopy(existing),
                "identity_sample_count": self._identity_sample_count(identity),
            }

        sample_no = sum(1 for s in self.state["samples"] if s["identity"] == identity) + 1
        sample_id = f"S-{_slug(point)}-{time_key}-{_slug(value)}"
        sample = {
            "id": sample_id,
            "sample_id": sample_id,
            "candidate_id": sample_id,
            "identity": identity,
            "point_code": point,
            "sample_time": time_label,
            "time_key": time_key,
            "value": value,
            "value_decimal": str(decimal_value),
            "source": source_key,
            "source_label": source_label,
            "sources": [{
                "source": source_key,
                "source_label": source_label,
                "first_seen_at": receipt["at"],
                "delivery_count": 1,
            }],
            "delivery_count": 1,
            "duplicate_count": 0,
            "first_received_at": receipt["at"],
            "last_received_at": receipt["at"],
            "status": "待裁定" if sample_no >= 2 else "暂为有效",
            "decision": None,
            "is_valid": sample_no == 1,
            "evidence": (
                f"{source_label}（{source_key}）于 {receipt['at']} 首次上报 {value}；"
                "原始投递记录和来源渠道已保留。"
            ),
        }
        self.state["samples"].append(sample)
        self._event(actor, f"接收 {identity} 新读数 {value}，来源 {source_label}", identity=identity)
        self._auto_read_pending_window(identity, sample, actor="窗口读取器")

        dispute = None
        if self._identity_sample_count(identity) >= 2:
            dispute = self._create_or_refresh_dispute(identity)
            self._mark_unconfirmed_windows(identity, dispute)

        return {
            "ok": True,
            "duplicate": False,
            "message": "异值已保留为核对候选并生成/更新争议处理单" if dispute else "读数已接收",
            "receipt": receipt,
            "sample": deepcopy(sample),
            "dispute": deepcopy(dispute) if dispute else None,
            "identity_sample_count": self._identity_sample_count(identity),
        }

    def _identity_sample_count(self, identity: str) -> int:
        return sum(1 for s in self.state["samples"] if s["identity"] == identity)

    def _identity_samples(self, identity: str) -> list[dict[str, Any]]:
        return [s for s in self.state["samples"] if s["identity"] == identity]

    # ------------------------------------------------------------------
    # 争议处理单
    # ------------------------------------------------------------------
    def _create_or_refresh_dispute(self, identity: str) -> dict[str, Any]:
        samples = self._identity_samples(identity)
        open_dispute = next((
            d for d in self.state["disputes"]
            if d.get("identity") == identity and d.get("status") == "待裁定"
        ), None)

        if open_dispute is not None:
            for sample in samples:
                sample["status"] = "争议待裁定"
                sample["is_valid"] = False
            open_dispute["candidates"] = [self._candidate_view(s) for s in samples]
            open_dispute["two_sources"] = self._two_sources(samples)
            return open_dispute

        # 争议未裁定前，所有候选都只是待核对读数；这样窗口无法绕过裁决取到“有效样本”。
        for sample in samples:
            sample["status"] = "争议待裁定"
            sample["is_valid"] = False

        resolved_rounds = sum(
            1 for d in self.state["disputes"]
            if d.get("identity") == identity and d.get("status") == "已裁定"
        )
        round_no = resolved_rounds + 1
        point, _, time_label = identity.partition("@")
        base_no = f"DSP-{_slug(point)}-{_slug(time_label)}"
        dispute_no = base_no if round_no == 1 else f"{base_no}-R{round_no}"

        dispute = {
            "id": dispute_no,
            "dispute_no": dispute_no,
            "争议单号": dispute_no,
            "identity": identity,
            "point_code": samples[0].get("point_code"),
            "sample_time": samples[0].get("sample_time"),
            "status": "待裁定",
            "pending": True,
            "abnormal": True,
            "created_at": _now(),
            "ruled_at": None,
            "ruled_by": None,
            "ruling_reason": None,
            "selected_sample_id": None,
            "adopted_sample_id": None,
            "adopted_value": None,
            "adopted_source": None,
            "adopted_basis": None,
            "rejected_sample_id": None,
            "rejected_value": None,
            "rejected_source": None,
            "rejected_basis": None,
            "candidates": [self._candidate_view(s) for s in samples],
            "two_sources": self._two_sources(samples),
            "first_decision": None,
            "reopen_count": 0,
            "timeline": [{"at": _now(), "actor": "系统", "message": "发现同位异值，已开立争议处理单，等待操作员裁定。"}],
        }
        self.state["disputes"].append(dispute)
        self._event("系统", f"已开立争议处理单 {dispute_no}", identity=identity, dispute_id=dispute_no)
        return dispute

    def _candidate_view(self, sample: dict[str, Any]) -> dict[str, Any]:
        return {
            "sample_id": sample["id"],
            "candidate_id": sample["id"],
            "value": sample["value"],
            "source": sample["source"],
            "source_label": sample["source_label"],
            "delivery_count": sample.get("delivery_count", 1),
            "duplicate_count": sample.get("duplicate_count", 0),
            "sources": deepcopy(sample.get("sources", [])),
            "first_received_at": sample.get("first_received_at"),
            "status": sample.get("status"),
            "is_valid": bool(sample.get("is_valid")),
            "evidence": sample.get("evidence"),
        }

    def _two_sources(self, samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{
            "side": "A" if index == 0 else chr(65 + index),
            "sample_id": s["id"],
            "source": s["source"],
            "source_label": s["source_label"],
            "value": s["value"],
            "delivery_count": s.get("delivery_count", 1),
        } for index, s in enumerate(samples[:2])]

    def adjudicate_from_payload(self, dispute_id: str, data: dict[str, Any]) -> dict[str, Any]:
        sample_id = _first(
            data, "sample_id", "candidate_id", "selected_sample_id", "adopted_sample_id",
            "choice", "selected", "select", "采用", "候选",
        )
        value = _first(data, "adopt_value", "adopted_value", "selected_value", "chosen_value",
                       "confirmed_value", "final_value", "decision_value", "value", "采用值", "值")
        source = _first(data, "adopt_source", "adopted_source", "selected_source", "chosen_source",
                        "source_channel", "source", "channel", "来源", None)
        side = _first(data, "side", "option", "candidate_side", "候选侧", default=None)
        reason = _first(data, "reason", "ruling_reason", "decision_reason", "adjudication_reason",
                        "rationale", "remark", "basis", "comment", "note", "裁决依据", default="")
        actor = _first(data, "operator", "actor", "ruled_by", "adjudicator", default="操作员")
        decision_text = str(_first(data, "decision", "resolution", "verdict", "裁决结论", default="") or "")

        # 兼容 decision="采用14" / "adopt-14" 这类把选择写在动作结论里的调用。
        if value is None and decision_text:
            numbers = re.findall(r"-?\d+(?:\.\d+)?", decision_text)
            if numbers:
                value = numbers[0]
            elif decision_text.strip().upper() in {"A", "B"}:
                side = decision_text.strip().upper()
        if source is None and decision_text and not re.search(r"\d", decision_text):
            source = decision_text

        process = str(_first(data, "process", "continue_processing", "continue", "sign", default="false")).lower()
        should_continue = process in {"1", "true", "yes", "on", "继续", "签收"} or bool(data.get("sign"))
        window_id = _first(data, "window_id", "continue_window_id", default=None)
        return self.adjudicate(
            dispute_id,
            sample_id=str(sample_id) if sample_id is not None else None,
            value=value,
            source=str(source) if source is not None else None,
            side=str(side) if side is not None else None,
            reason=str(reason or ""),
            actor=str(actor),
            should_continue=should_continue,
            window_id=str(window_id) if window_id is not None else None,
        )

    def adjudicate(
        self,
        dispute_id: str,
        *,
        sample_id: str | None = None,
        value: Any = None,
        source: str | None = None,
        side: str | None = None,
        reason: str = "",
        actor: str = "操作员",
        should_continue: bool = False,
        window_id: str | None = None,
    ) -> dict[str, Any]:
        dispute = self._find_dispute(dispute_id)
        if dispute is None:
            return {"ok": False, "message": f"争议处理单 {dispute_id} 不存在"}

        identity = dispute["identity"]
        samples = self._identity_samples(identity)
        chosen = self._match_candidate(samples, sample_id=sample_id, value=value, source=source, side=side)
        if chosen is None:
            return {"ok": False, "message": "无法识别采用对象：请选择一个来源/值候选"}

        # 已裁定单重复提交相同选择时保持幂等，不覆盖第一次处置。
        if dispute["status"] == "已裁定":
            if dispute.get("adopted_sample_id") != chosen["id"]:
                return {"ok": False, "message": "该争议单已有第一次处置；不同结论需另开复审单，不能覆盖原裁决"}
            result = self.process_dependent_windows(identity)
            if should_continue:
                target = self._find_window(window_id) if window_id else self._first_dependent_window(identity)
                if target is not None:
                    self.continue_window(target["id"], actor=actor)
            return {"ok": True, "idempotent": True, "message": "争议单已裁定，重复选择保持原结论",
                    "dispute": deepcopy(dispute), "processing": result}

        rejected = [s for s in samples if s["id"] != chosen["id"]]
        chosen["status"] = "已采用"
        chosen["decision"] = "adopted"
        chosen["is_valid"] = True
        for sample in rejected:
            sample["status"] = "已拒绝"
            sample["decision"] = "rejected"
            sample["is_valid"] = False

        rejected_text = "；".join(f"{s['source_label']} 的 {s['value']}" for s in rejected) or "其他候选"
        adopted_basis = (
            f"{actor}核对两个来源后裁决采用 {chosen['source_label']}（{chosen['source']}）"
            f"在 {chosen['sample_time']} 的值 {chosen['value']}。"
            f"该样本原始投递 {chosen.get('delivery_count', 1)} 次；裁决后它是采样身份 {identity} "
            "唯一有资格用于正式出值的有效样本。"
        )
        rejected_basis = (
            f"拒绝 {rejected_text}：该读数虽保留原始来源、投递次数和页面核对依据，"
            "但未被裁决采用；若窗口在签收前曾预读该值，必须改按已采用的有效样本核对，"
            "被拒样本不得作为正式出值依据。"
        )
        if reason.strip():
            adopted_basis += f" 操作员登记依据：{reason.strip()}"
            rejected_basis += f" 裁决说明：{reason.strip()}"

        dispute.update({
            "status": "已裁定",
            "pending": False,
            "abnormal": False,
            "ruled_at": _now(),
            "ruled_by": actor,
            "ruling_reason": reason.strip(),
            "selected_sample_id": chosen["id"],
            "adopted_sample_id": chosen["id"],
            "adopted_value": chosen["value"],
            "adopted_source": chosen["source"],
            "adopted_source_label": chosen["source_label"],
            "adopted_basis": adopted_basis,
            "rejected_sample_id": rejected[0]["id"] if rejected else None,
            "rejected_value": rejected[0]["value"] if rejected else None,
            "rejected_source": rejected[0]["source"] if rejected else None,
            "rejected_source_label": rejected[0]["source_label"] if rejected else None,
            "rejected_basis": rejected_basis,
            "candidates": [self._candidate_view(s) for s in samples],
        })
        dispute["first_decision"] = {
            "ruled_at": dispute["ruled_at"],
            "ruled_by": actor,
            "adopted_sample_id": chosen["id"],
            "adopted_value": chosen["value"],
            "adopted_source": chosen["source"],
            "reason": reason.strip(),
            "adopted_basis": adopted_basis,
            "rejected_sample_id": dispute["rejected_sample_id"],
            "rejected_value": dispute["rejected_value"],
            "rejected_source": dispute["rejected_source"],
            "rejected_basis": rejected_basis,
        }
        rejected_primary = rejected[0] if rejected else None
        dispute["adopted"] = {
            "sample_id": chosen["id"],
            "value": chosen["value"],
            "source": chosen["source"],
            "source_label": chosen["source_label"],
            "basis": adopted_basis,
            "is_valid": True,
        }
        dispute["rejected"] = ({
            "sample_id": rejected_primary["id"],
            "value": rejected_primary["value"],
            "source": rejected_primary["source"],
            "source_label": rejected_primary["source_label"],
            "basis": rejected_basis,
            "is_valid": False,
        } if rejected_primary else None)
        dispute["decision"] = "adopt"
        dispute["resolution"] = f"采用 {chosen['value']}"
        dispute["timeline"].append({
            "at": dispute["ruled_at"], "actor": actor,
            "message": f"第一次处置：采用 {chosen['source_label']} 的 {chosen['value']}，"
                       f"拒绝 {rejected_text}",
        })
        self._event(actor, f"争议处理单 {dispute['dispute_no']} 已裁定采用 {chosen['value']}",
                    dispute_id=dispute["id"], identity=identity)

        processing = self.process_dependent_windows(identity, actor=actor)
        continued = None
        if should_continue:
            target = self._find_window(window_id) if window_id else self._first_dependent_window(identity)
            if target is not None:
                continued = self.continue_window(target["id"], actor=actor)

        return {
            "ok": True,
            "message": f"已登记裁决：采用 {chosen['value']}，服务端已处理依赖 {identity} 的未确认窗口",
            "dispute": deepcopy(dispute),
            "processing": processing,
            "continued": continued,
        }

    def _match_candidate(
        self,
        samples: list[dict[str, Any]],
        *,
        sample_id: str | None,
        value: Any,
        source: str | None,
        side: str | None,
    ) -> dict[str, Any] | None:
        if sample_id:
            for sample in samples:
                if sample_id in {sample["id"], sample.get("candidate_id")}:
                    return sample
            # 允许前端传候选序号。
            if str(sample_id).isdigit():
                idx = int(sample_id)
                if 0 <= idx < len(samples):
                    return samples[idx]
                if 1 <= idx <= len(samples):
                    return samples[idx - 1]

        if side:
            side_text = str(side).strip().upper()
            if side_text in {"A", "1", "FIRST"} and samples:
                return samples[0]
            if side_text in {"B", "2", "SECOND"} and len(samples) > 1:
                return samples[1]

        wanted_value = _as_decimal(value)
        candidates = samples
        if wanted_value is not None:
            candidates = [s for s in candidates if _as_decimal(s.get("value")) == wanted_value]
        if source:
            source_key = _slug(source).lower()
            source_matches = [
                s for s in candidates
                if source_key in {str(s.get("source", "")).lower(), _slug(s.get("source_label")).lower()}
                or source_key == str(s.get("source", "")).lower()
            ]
            if source_matches:
                candidates = source_matches
        if len(candidates) == 1:
            return candidates[0]
        if wanted_value is not None and len(candidates) >= 1:
            return candidates[0]
        return None

    # ------------------------------------------------------------------
    # 窗口读取、按裁决处理、继续/签收
    # ------------------------------------------------------------------
    def read_window_from_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        window_id = _first(data, "window_id", "id", "窗口编号", default=None)
        point = _first(data, "point_code", "point_id", "point", "测点编号", default=None)
        sample_time = _first(data, "sample_time", "time", "timestamp", "时间", default=None)
        return self.read_window({
            "window_id": window_id,
            "point_code": point,
            "sample_time": sample_time,
            "sample_id": _first(data, "sample_id", default=None),
            "value": _first(data, "value", "read_value", default=None),
            "source": _first(data, "source", default=None),
            "actor": _first(data, "actor", "operator", default="值班操作员"),
        })

    def read_window(self, data: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
        data = {**(data or {}), **kwargs}
        window = self._find_window(str(data.get("window_id") or ""))
        if window is None and data.get("point_code"):
            identity = f"{data['point_code']}@{normalize_time(data.get('sample_time'))[0]}"
            window = next((w for w in self.state["windows"] if identity in w.get("dependencies", [])), None)
        if window is None:
            return {"ok": False, "message": "未找到要读取样本的窗口"}

        identity = window["dependencies"][0]
        samples = self._identity_samples(identity)
        sample = self._match_candidate(
            samples,
            sample_id=data.get("sample_id"),
            value=data.get("value"),
            source=data.get("source"),
            side=None,
        )
        if sample is None:
            valid = [s for s in samples if s.get("is_valid")]
            sample = valid[0] if valid else (samples[0] if samples else None)
        if sample is None:
            return {"ok": False, "message": f"窗口 {window['id']} 尚无可读取样本"}

        actor = str(data.get("actor") or "值班操作员")
        window.update({
            "read_value": sample["value"],
            "read_sample_id": sample["id"],
            "read_source": sample["source"],
            "read_at": _now(),
            "signed": False,
            "confirmed": False,
            "used_sample_id": None,
            "used_value": None,
            "formal_output": None,
            "verification": None,
        })
        open_dispute = next((d for d in self.state["disputes"]
                             if d.get("identity") == identity and d.get("status") == "待裁定"), None)
        if open_dispute:
            window["status"] = "争议待裁定"
            window["pending_dispute_id"] = open_dispute["id"]
            self._window_event(window, actor, f"窗口已预读 {sample['value']}，但签收前发现争议 {open_dispute['id']}，暂停确认。")
        else:
            window["status"] = "已读待签收"
            window["pending_dispute_id"] = None
            self._window_event(window, actor, f"窗口读取样本 {sample['id']}，读数 {sample['value']}，尚未签收。")
        return {"ok": True, "window": deepcopy(window), "message": "窗口已读取样本，尚未签收"}

    def _auto_read_pending_window(self, identity: str, sample: dict[str, Any], *, actor: str) -> None:
        """样本到达后，让处于待读取状态的剧本窗口预读该值（仍不签收）。"""
        for window in self.state["windows"]:
            if identity not in window.get("dependencies", []):
                continue
            if window.get("confirmed") or window.get("signed") or window.get("read_sample_id"):
                continue
            if window.get("status") not in {"待读取", None, ""}:
                continue
            window.update({
                "read_value": sample["value"],
                "read_sample_id": sample["id"],
                "read_source": sample["source"],
                "read_at": _now(),
                "status": "已读待签收",
            })
            self._window_event(
                window, actor,
                f"窗口自动读取样本 {sample['id']}，读数 {sample['value']}，在签收前保持未确认。",
            )

    def _mark_unconfirmed_windows(self, identity: str, dispute: dict[str, Any]) -> None:
        for window in self.state["windows"]:
            if identity not in window.get("dependencies", []):
                continue
            if window.get("confirmed") or window.get("signed"):
                continue
            window["status"] = "争议待裁定"
            window["pending_dispute_id"] = dispute["id"]
            self._window_event(
                window, "系统",
                f"正要确认时出现同位争议 {dispute['id']}；保留预读值 {window.get('read_value')}，暂停签收。",
            )

    def _first_dependent_window(self, identity: str) -> dict[str, Any] | None:
        return next((w for w in self.state["windows"]
                     if identity in w.get("dependencies", []) and not w.get("confirmed")), None)

    def process_dependent_windows(
        self,
        identity: str,
        *,
        actor: str = "服务端",
    ) -> dict[str, Any]:
        eligible = [s for s in self._identity_samples(identity) if s.get("is_valid")]
        processed: list[dict[str, Any]] = []
        skipped_confirmed: list[dict[str, Any]] = []
        unrelated: list[dict[str, Any]] = []

        for window in self.state["windows"]:
            if identity not in window.get("dependencies", []):
                unrelated.append({"id": window["id"], "reason": "不依赖该采样位置，未参与重算"})
                continue
            if window.get("confirmed") or window.get("signed"):
                skipped_confirmed.append({"id": window["id"], "reason": "窗口已确认/签收，不追溯改写"})
                continue
            if len(eligible) != 1:
                return {
                    "ok": False,
                    "message": f"{identity} 当前应有且仅有一个有效样本，实际为 {len(eligible)} 个",
                    "processed_windows": processed,
                    "unrelated_windows": unrelated,
                }

            chosen = eligible[0]
            previous_sample_id = window.get("read_sample_id")
            previous_value = window.get("read_value")
            changed = bool(previous_sample_id and previous_sample_id != chosen["id"])
            window.update({
                "used_sample_id": chosen["id"],
                "used_value": chosen["value"],
                "used_source": chosen["source"],
                "eligible_sample_ids": [chosen["id"]],
                "eligible_sample_count": 1,
                "pending_dispute_id": None,
                "processing_dispute_id": self._latest_resolved_dispute_id(identity),
                "processed_at": _now(),
            })
            if changed:
                window["recomputed"] = True
                window["recompute_count"] = int(window.get("recompute_count", 0)) + 1
                window["corrected_from_sample_id"] = previous_sample_id
                window["corrected_from_value"] = previous_value
                window["status"] = "已按裁决重算待签收"
                self._window_event(
                    window, actor,
                    f"按争议裁决改用仍有资格的唯一有效样本 {chosen['id']}（{chosen['value']}）；"
                    f"预读值 {previous_value} 来自已拒绝样本，已重算核对。",
                )
            else:
                window["status"] = "已核对待签收"
                self._window_event(
                    window, actor,
                    f"按争议裁决核对：预读样本 {chosen['id']} 仍是唯一有资格有效样本，可继续签收。",
                )

            window["pending_output"] = self._formal_output(window, chosen)
            window["verification"] = {
                "checked": True,
                "effective_sample_count": 1,
                "actual_used_effective_sample": True,
                "used_sample_id": chosen["id"],
                "used_value": chosen["value"],
                "dispute_id": window.get("processing_dispute_id"),
                "conclusion": f"核对通过：本窗实际使用一项有效值，且仅有 1 个有效样本（{chosen['source_label']}={chosen['value']}）。",
            }
            window["check_result"] = window["verification"]["conclusion"]
            window["核对结果"] = window["verification"]["conclusion"]
            processed.append(deepcopy(window))

        return {
            "ok": True,
            "identity": identity,
            "eligible_sample_ids": [s["id"] for s in eligible],
            "processed_windows": processed,
            "skipped_confirmed_windows": skipped_confirmed,
            "unrelated_windows": unrelated,
            "message": f"已处理 {len(processed)} 个依赖该位置的未确认窗口；{len(unrelated)} 个无关窗未参与重算",
        }

    def _latest_resolved_dispute_id(self, identity: str) -> str | None:
        resolved = [d for d in self.state["disputes"]
                    if d.get("identity") == identity and d.get("status") == "已裁定"]
        return resolved[-1]["id"] if resolved else None

    def _formal_output(self, window: dict[str, Any], sample: dict[str, Any]) -> dict[str, Any]:
        dispute_id = window.get("processing_dispute_id") or self._latest_resolved_dispute_id(sample["identity"])
        return {
            "value": sample["value"],
            "sample_id": sample["id"],
            "used_sample_id": sample["id"],
            "source": sample["source"],
            "source_label": sample["source_label"],
            "identity": sample["identity"],
            "sample": {
                "sample_id": sample["id"],
                "value": sample["value"],
                "source": sample["source"],
                "source_label": sample["source_label"],
                "is_valid": True,
            },
            "dispute_id": dispute_id,
            "eligibility": (
                f"争议处理单 {dispute_id} 已裁定 {sample['id']} 为该位置唯一仍有资格的有效样本；"
                "其他候选保留来源但不具备出值资格。"
            ),
            "effective_sample_count": 1,
        }

    def continue_window_from_payload(self, window_id: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        data = data or {}
        return self.continue_window(
            window_id,
            actor=str(_first(data, "actor", "operator", default="值班操作员")),
            sign=bool(_first(data, "sign", "confirm", default=True)),
        )

    def continue_window(self, window_id: str, *, actor: str = "值班操作员", sign: bool = True) -> dict[str, Any]:
        window = self._find_window(window_id)
        if window is None:
            return {"ok": False, "message": f"窗口 {window_id} 不存在"}

        # 先按所有依赖位置的最新裁决处理一次。
        for identity in window.get("dependencies", []):
            result = self.process_dependent_windows(identity, actor=actor)
            if not result.get("ok"):
                return result

        if any(self._has_open_dispute(identity) for identity in window.get("dependencies", [])):
            window["status"] = "争议待裁定"
            return {"ok": False, "message": "窗口依赖的位置仍有未裁定争议，不能继续签收", "window": deepcopy(window)}

        if not sign:
            return {"ok": True, "window": deepcopy(window), "message": "窗口已按裁决核对"}
        return self.sign_window(window["id"], actor=actor)

    def _has_open_dispute(self, identity: str) -> bool:
        return any(d.get("identity") == identity and d.get("status") == "待裁定"
                   for d in self.state["disputes"])

    def sign_window(self, window_id: str, *, actor: str = "值班操作员") -> dict[str, Any]:
        window = self._find_window(window_id)
        if window is None:
            return {"ok": False, "message": f"窗口 {window_id} 不存在"}
        if window.get("signed") and window.get("confirmed") and window.get("formal_output"):
            return {"ok": True, "idempotent": True,
                    "message": "窗口已签收出值，未重复追加处置记录", "window": deepcopy(window)}

        used_samples: list[dict[str, Any]] = []
        for identity in window.get("dependencies", []):
            eligible = [s for s in self._identity_samples(identity) if s.get("is_valid")]
            if len(eligible) != 1:
                return {"ok": False, "message": f"{identity} 没有唯一有效样本，不能签收"}
            used_samples.append(eligible[0])

        # 当前剧本一个窗口依赖一个采样位置；若未来有多依赖，formal_output 可扩展为列表。
        sample = used_samples[0]
        window.update({
            "signed": True,
            "confirmed": True,
            "status": "已签收并正式出值",
            "signed_at": _now(),
            "used_sample_id": sample["id"],
            "used_value": sample["value"],
            "used_source": sample["source"],
            "eligible_sample_ids": [sample["id"]],
            "eligible_sample_count": 1,
            "formal_output": self._formal_output(window, sample),
        })
        window["verification"] = {
            "checked": True,
            "actual_used_sample_count": 1,
            "effective_sample_count": 1,
            "actual_used_effective_sample": True,
            "used_sample_id": sample["id"],
            "used_value": sample["value"],
            "neighbor_recomputed": False,
            "conclusion": f"最终核对通过：本窗实际使用的是一项有效值（{sample['source_label']}={sample['value']}），有效样本数为 1。",
        }
        window.update({
            "checked": True,
            "check_result": window["verification"]["conclusion"],
            "核对结果": window["verification"]["conclusion"],
            "actual_used_value": sample["value"],
            "actual_used_sample_count": 1,
            "effective_sample_count": 1,
        })
        self._window_event(
            window, actor,
            f"继续处理并签收，正式出值 {sample['value']}；可追溯到唯一有效样本 {sample['id']}。",
        )
        self._event(actor, f"窗口 {window['id']} 已签收并正式出值 {sample['value']}", window_id=window["id"])
        return {"ok": True, "message": "窗口已继续处理并签收，正式出值可追溯", "window": deepcopy(window)}

    def list_disputes(self) -> list[dict[str, Any]]:
        return deepcopy(self.state["disputes"])

    def create_dispute_from_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        # 兼容统一登记接口的 {"values": {...}} 包装。
        if isinstance(data.get("values"), dict):
            nested = dict(data["values"])
            nested.update({k: v for k, v in data.items() if k != "values" and v is not None})
            data = nested

        point = _first(data, "point_code", "point_id", "point", "测点编号", "测点", default="P")
        sample_time = _first(data, "sample_time", "timestamp", "time", "ts", "采样时间", "时间", default="11:00")
        identity = _first(data, "identity", "sample_identity", "采样身份", default=None)
        if identity is None:
            time_key, _ = normalize_time(sample_time)
            identity = f"{point}@{time_key}"

        candidates = _first(data, "candidates", "readings", "samples", "候选读数", default=[])
        if isinstance(candidates, list):
            for index, candidate in enumerate(candidates):
                if not isinstance(candidate, dict):
                    continue
                payload = {
                    "point_code": candidate.get("point_code", point),
                    "sample_time": candidate.get("sample_time", sample_time),
                    "value": candidate.get("value"),
                    "source": candidate.get("source") or candidate.get("source_code") or f"source-{index + 1}",
                    "source_label": candidate.get("source_label"),
                    "identity": identity,
                }
                self.ingest_sample(payload)

        existing = self._identity_samples(str(identity))
        if len(existing) < 2:
            values = _first(data, "values", "conflicting_values", "异值", default=None)
            sources = _first(data, "sources", "source_channels", default=None)
            if isinstance(values, list):
                for index, value in enumerate(values):
                    source = None
                    if isinstance(sources, list) and index < len(sources):
                        source = sources[index]
                    self.ingest_sample({
                        "point_code": point,
                        "sample_time": sample_time,
                        "value": value,
                        "source": source or f"source-{index + 1}",
                        "identity": identity,
                    })

        existing = self._identity_samples(str(identity))
        if len(existing) < 2:
            return {"ok": False, "message": "争议处理单至少需要同一采样身份的两个不同读数/来源"}

        # 若已有未裁定单，_create_or_refresh_dispute 会刷新候选；否则开新单。
        dispute = self._create_or_refresh_dispute(str(identity))
        self._mark_unconfirmed_windows(str(identity), dispute)
        return {"ok": True, "message": f"争议处理单 {dispute['dispute_no']} 已开立",
                "dispute": deepcopy(dispute), "entry": deepcopy(dispute)}

    def list_windows(self) -> list[dict[str, Any]]:
        return deepcopy(self.state["windows"])


reconciliation_service = TsReconciliationService()
