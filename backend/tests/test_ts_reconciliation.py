"""同位时序读数冲突、裁决与窗口核对的验收规则测试。"""
from __future__ import annotations

import pytest

from app.services.ts_point import TsPointService
from app.services.ts_reconciliation import reconciliation_service


@pytest.fixture(autouse=True)
def restore_demo_state():
    yield
    # 内存单例会跨测试保留；本文件结束后恢复应用启动时的验收场景。
    reconciliation_service.reset("conflict")


def test_same_identity_same_value_is_idempotent_but_other_value_opens_dispute():
    state = reconciliation_service.reset("stage")
    assert state["ok"] is True

    first = reconciliation_service.ingest_from_payload({
        "point_code": "P", "sample_time": "11:00", "value": "10", "source": "source-A",
    })
    second = reconciliation_service.ingest_from_payload({
        "point_code": "P", "sample_time": "11:00", "value": 10.0, "source": "source-A",
    })

    assert first["ok"] and second["ok"]
    assert second["duplicate"] is True
    identity_samples = [s for s in reconciliation_service.state["samples"] if s["identity"] == "P@1100"]
    assert len(identity_samples) == 1
    assert identity_samples[0]["delivery_count"] == 2

    read = reconciliation_service.read_window({"window_id": "WIN-P-1100"})
    assert read["ok"] is True
    assert read["window"]["read_value"] == 10
    assert read["window"]["signed"] is False

    third = reconciliation_service.ingest_from_payload({
        "point_code": "P", "sample_time": "11:00", "value": "14", "source": "source-B",
    })
    assert third["ok"] is True
    assert third["dispute"]["dispute_no"] == "DSP-P-1100"

    dispute = reconciliation_service.list_disputes()[0]
    window = reconciliation_service.get_window("WIN-P-1100")
    assert dispute["status"] == "待裁定"
    assert window["status"] == "争议待裁定"
    assert window["read_value"] == 10  # 预读痕迹保留，但不能签收。


def test_adopt_14_makes_one_valid_sample_and_formal_window_output_traceable():
    reconciliation_service.reset("ready")
    reconciliation_service.ingest_from_payload({
        "point_code": "P", "sample_time": "11:00", "value": 14, "source": "source-B",
    })

    ruling = reconciliation_service.adjudicate(
        "DSP-P-1100", value=14, reason="现场双通道复核", actor="操作员",
    )
    assert ruling["ok"] is True
    assert ruling["dispute"]["adopted_value"] == 14
    assert ruling["dispute"]["rejected_value"] == 10
    assert "采用依据" in ruling["dispute"]["adopted_basis"] or "唯一有资格" in ruling["dispute"]["adopted_basis"]
    assert "拒绝" in ruling["dispute"]["rejected_basis"]

    valid = [s for s in reconciliation_service.state["samples"]
             if s["identity"] == "P@1100" and s["is_valid"]]
    assert len(valid) == 1
    assert valid[0]["value"] == 14

    result = reconciliation_service.continue_window("WIN-P-1100")
    assert result["ok"] is True
    window = result["window"]
    assert window["status"] == "已签收并正式出值"
    assert window["formal_output"]["value"] == 14
    assert window["formal_output"]["sample_id"] == "S-P-1100-14"
    assert window["formal_output"]["dispute_id"] == "DSP-P-1100"
    assert "仍有资格" in window["formal_output"]["eligibility"]
    assert window["verification"]["actual_used_sample_count"] == 1
    assert "一项有效值" in window["verification"]["conclusion"]

    side = reconciliation_service.get_window("WIN-P-1200-SIDE")
    assert side["used_value"] == 20
    assert side["recompute_count"] == 0
    assert side["status"] == "已签收出值"

    reopened = reconciliation_service.get_dispute("DSP-P-1100", reopen=True)
    assert reopened["first_decision"]["adopted_value"] == 14
    assert reopened["reopen_count"] == 1
    assert reopened["linked_windows"][0]["formal_output"]["sample_id"] == "S-P-1100-14"


def test_ts_point_service_exposes_semantic_aliases():
    service = TsPointService()
    state = service.reset_reconciliation("stage")
    assert state["ok"] is True
    result = service.report_sample("P", "11:00", 10, "channel-A")
    assert result["ok"] is True
    assert callable(service.adjudicate_dispute)
    assert callable(service.continue_window)
