"""争议处理单通用资源适配。

核心规则在 TsReconciliationService；本类把争议单映射成平台通用资源结构，
使 ``/api/ts_dispute`` 也能走统一列表、详情和动作接口。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.services.ts_reconciliation import reconciliation_service


class TsDisputeService(BaseModuleService):
    MODULE = "ts_dispute"
    ENTITY = "争议处理单"
    LIST_FIELDS = [
        "争议单号", "测点编号", "采样时间", "采样身份", "候选来源", "候选值",
        "采用来源", "采用值", "拒绝来源", "拒绝值", "裁决人", "裁决依据",
    ]
    REQUIRED_FIELDS = ["测点编号", "采样时间"]
    STATUS_ORDER = ["待裁定", "已裁定"]
    ACTION_RULES = {"查看": "待裁定", "裁定采用14": "已裁定", "继续处理": "已裁定"}

    def _mapped_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for index, dispute in enumerate(reconciliation_service.list_disputes(), start=1):
            candidates = dispute.get("candidates", [])
            rows.append({
                "id": index,
                "争议单号": dispute.get("dispute_no") or dispute.get("id"),
                "测点编号": dispute.get("point_code"),
                "采样时间": dispute.get("sample_time"),
                "采样身份": dispute.get("identity"),
                "候选来源": " / ".join(str(c.get("source_label") or c.get("source")) for c in candidates),
                "候选值": " / ".join(str(c.get("value")) for c in candidates),
                "采用来源": dispute.get("adopted_source_label") or dispute.get("adopted_source") or "",
                "采用值": dispute.get("adopted_value") if dispute.get("adopted_value") is not None else "",
                "拒绝来源": dispute.get("rejected_source_label") or dispute.get("rejected_source") or "",
                "拒绝值": dispute.get("rejected_value") if dispute.get("rejected_value") is not None else "",
                "裁决人": dispute.get("ruled_by") or "",
                "裁决依据": dispute.get("ruling_reason") or dispute.get("adopted_basis") or "",
                "status": dispute.get("status", "待裁定"),
                "pending": dispute.get("status") != "已裁定",
                "abnormal": dispute.get("status") != "已裁定",
            })
        return rows

    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._mapped_rows()
        for field, expected in (filters or {}).items():
            if field in self.LIST_FIELDS:
                rows = [row for row in rows
                        if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            needle = keyword.casefold()
            rows = [row for row in rows if needle in str(row.get("争议单号", "")).casefold()
                    or needle in str(row.get("测点编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return next((row for row in self._mapped_rows() if int(row["id"]) == int(entry_id)), None)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        result = reconciliation_service.create_dispute_from_payload(dict(values or {}))
        if not result.get("ok"):
            return None, [str(result.get("message", "争议处理单登记失败"))]
        dispute_no = result["dispute"]["dispute_no"]
        entry = next((row for row in self._mapped_rows() if row["争议单号"] == dispute_no), result["dispute"])
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        return self.run_action_detailed(entry_id, action, {})

    def run_action_detailed(
        self,
        entry_id: int,
        action: str,
        extra: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any] | None, str]:
        entry = self.get_entry(entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在"

        extra = dict(extra or {})
        if extra.get("remark") and not extra.get("reason"):
            extra["reason"] = extra["remark"]
        dispute_no = str(entry["争议单号"])
        action_name = action.strip().lower()
        has_choice = any(extra.get(key) not in (None, "") for key in (
            "sample_id", "value", "adopted_value", "selected_value", "chosen_value", "side",
        ))
        is_adjudication = (
            any(token in action for token in ("采用", "裁定", "裁决"))
            or action_name in {"adopt", "decision", "resolve", "rule", "decide", "adjudicate"}
            or action_name.startswith(("adopt-", "resolve-", "rule-"))
            or has_choice
        )

        if is_adjudication:
            payload = dict(extra)
            payload.setdefault("decision", action)
            # 动作字符串中明确带数字时，adjudicate_from_payload 也能从 decision 解析。
            result = reconciliation_service.adjudicate_from_payload(dispute_no, payload)
            if not result.get("ok"):
                return None, str(result.get("message"))
            refreshed = self.get_entry(entry_id)
            return refreshed, str(result.get("message"))

        if any(token in action for token in ("继续", "签收", "处理")) or action_name in {
            "continue", "process", "sign", "confirm",
        }:
            dispute = reconciliation_service.get_dispute(dispute_no)
            if dispute is None:
                return None, "争议单不存在"
            identity = dispute["identity"]
            window_id = extra.get("window_id")
            window = reconciliation_service._find_window(str(window_id)) if window_id else None  # noqa: SLF001
            if window is None:
                window = reconciliation_service._first_dependent_window(identity)  # noqa: SLF001
            if window is None:
                return entry, "没有需要继续处理的未确认窗口"
            result = reconciliation_service.continue_window_from_payload(window["id"], extra)
            if not result.get("ok"):
                return None, str(result.get("message"))
            return self.get_entry(entry_id), str(result.get("message"))

        return None, f"动作「{action}」不属于{self.MODULE}可执行范围"
