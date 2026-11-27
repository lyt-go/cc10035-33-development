"""时序测点同位冲突/争议处理单的 HTTP 接口。

资源路由只负责通用 CRUD；本路由承载样本上报、裁决和窗口继续处理等动作。
为兼容不同调用习惯，同一领域动作暴露若干语义别名。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.services.ts_reconciliation import reconciliation_service

router = APIRouter(tags=["时序争议处理"])


async def _payload(request: Request) -> dict[str, Any]:
    data: dict[str, Any] = dict(request.query_params)
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - 无 JSON body 时 query/form 参数仍可用
        body = None
    if isinstance(body, dict):
        for key, value in body.items():
            if value is not None:
                data[key] = value
    return data


# ----------------------------------------------------------------------
# 状态查询
# ----------------------------------------------------------------------
@router.get("/api/ts_point/reconciliation/state")
@router.get("/api/ts-point/reconciliation/state")
@router.get("/api/ts/reconciliation/state")
def reconciliation_state() -> dict[str, Any]:
    return reconciliation_service.snapshot()


@router.get("/api/ts_point/disputes")
@router.get("/api/ts-point/disputes")
@router.get("/api/ts_disputes")
@router.get("/api/disputes")
def list_disputes() -> dict[str, Any]:
    return {"ok": True, "items": reconciliation_service.list_disputes(),
            "total": len(reconciliation_service.state["disputes"]),
            "page": 1, "size": 200}


@router.post("/api/ts_point/disputes")
@router.post("/api/ts-point/disputes")
@router.post("/api/ts_disputes")
@router.post("/api/disputes")
async def create_dispute(request: Request) -> dict[str, Any]:
    data = await _payload(request)
    return reconciliation_service.create_dispute_from_payload(data)


@router.get("/api/ts_point/windows")
@router.get("/api/ts-point/windows")
def list_windows() -> dict[str, Any]:
    return {"ok": True, "items": reconciliation_service.list_windows(),
            "total": len(reconciliation_service.state["windows"])}


@router.get("/api/ts_point/disputes/{dispute_id}")
@router.get("/api/ts-point/disputes/{dispute_id}")
@router.get("/api/disputes/{dispute_id}")
def get_dispute(dispute_id: str, reopen: bool = False) -> dict[str, Any]:
    dispute = reconciliation_service.get_dispute(dispute_id, reopen=reopen)
    if dispute is None:
        return {"ok": False, "message": f"争议处理单 {dispute_id} 不存在"}
    return {"ok": True, "dispute": dispute, "entry": dispute}


@router.get("/api/ts_point/windows/{window_id}")
@router.get("/api/ts-point/windows/{window_id}")
def get_window(window_id: str) -> dict[str, Any]:
    window = reconciliation_service.get_window(window_id)
    if window is None:
        return {"ok": False, "message": f"窗口 {window_id} 不存在"}
    return {"ok": True, "window": window, "entry": window}


# ----------------------------------------------------------------------
# 样本上报：同值幂等，异值开单
# ----------------------------------------------------------------------
@router.get("/api/ts_point/samples")
@router.get("/api/ts_point/readings")
@router.get("/api/ts-point/samples")
@router.get("/api/samples")
def list_samples() -> dict[str, Any]:
    items = reconciliation_service.state["samples"]
    return {"ok": True, "items": items, "total": len(items), "page": 1, "size": 200}


@router.post("/api/ts_point/samples")
@router.post("/api/ts_point/readings")
@router.post("/api/ts_point/observations")
@router.post("/api/ts_point/ingest")
@router.post("/api/ts-point/samples")
@router.post("/api/ts-point/readings")
@router.post("/api/samples/report")
@router.post("/api/readings")
async def ingest_sample(request: Request) -> dict[str, Any]:
    data = await _payload(request)
    return reconciliation_service.ingest_from_payload(data)


# ----------------------------------------------------------------------
# 裁决
# ----------------------------------------------------------------------
@router.post("/api/ts_point/disputes/{dispute_id}/adjudicate")
@router.post("/api/ts_point/disputes/{dispute_id}/decision")
@router.post("/api/ts_point/disputes/{dispute_id}/resolve")
@router.post("/api/ts_point/disputes/{dispute_id}/rule")
@router.post("/api/ts-point/disputes/{dispute_id}/adjudicate")
@router.post("/api/disputes/{dispute_id}/adjudicate")
@router.post("/api/disputes/{dispute_id}/resolve")
async def adjudicate_dispute(dispute_id: str, request: Request) -> dict[str, Any]:
    data = await _payload(request)
    return reconciliation_service.adjudicate_from_payload(dispute_id, data)


@router.post("/api/ts_point/conflicts/{dispute_id}/adjudicate")
@router.post("/api/ts_point/conflicts/{dispute_id}/decision")
@router.post("/api/ts_point/conflicts/{dispute_id}/resolve")
async def adjudicate_conflict_alias(dispute_id: str, request: Request) -> dict[str, Any]:
    data = await _payload(request)
    return reconciliation_service.adjudicate_from_payload(dispute_id, data)


@router.post("/api/ts_point/reconciliation/adjudicate")
async def adjudicate_from_body(request: Request) -> dict[str, Any]:
    data = await _payload(request)
    dispute_id = str(data.get("dispute_id") or data.get("id") or data.get("dispute_no") or "")
    return reconciliation_service.adjudicate_from_payload(dispute_id, data)


@router.post("/api/ts_point/reconciliation/continue")
async def continue_from_body(request: Request) -> dict[str, Any]:
    data = await _payload(request)
    window_id = str(data.get("window_id") or data.get("id") or "WIN-P-1100")
    return reconciliation_service.continue_window_from_payload(window_id, data)


@router.post("/api/ts_point/disputes/{dispute_id}/reopen")
@router.post("/api/disputes/{dispute_id}/reopen")
async def reopen_dispute(dispute_id: str, request: Request) -> dict[str, Any]:
    data = await _payload(request)
    actor = str(data.get("operator") or data.get("actor") or "操作员")
    dispute = reconciliation_service.get_dispute(dispute_id, reopen=True, actor=actor)
    if dispute is None:
        return {"ok": False, "message": f"争议处理单 {dispute_id} 不存在"}
    return {"ok": True, "dispute": dispute, "entry": dispute, "message": "已重新打开第一次处置和后续结果"}


# ----------------------------------------------------------------------
# 窗口读取/继续/签收
# ----------------------------------------------------------------------
@router.post("/api/ts_point/windows/read")
@router.post("/api/ts-point/windows/read")
async def read_window(request: Request) -> dict[str, Any]:
    data = await _payload(request)
    return reconciliation_service.read_window_from_payload(data)


@router.post("/api/ts_point/windows/{window_id}/continue")
@router.post("/api/ts_point/windows/{window_id}/process")
@router.post("/api/ts_point/windows/{window_id}/confirm")
@router.post("/api/ts_point/windows/{window_id}/sign")
@router.post("/api/ts-point/windows/{window_id}/continue")
async def continue_window(window_id: str, request: Request) -> dict[str, Any]:
    data = await _payload(request)
    if str(request.url.path).endswith("/sign"):
        data["sign"] = True
    return reconciliation_service.continue_window_from_payload(window_id, data)


# ----------------------------------------------------------------------
# 剧本/重置
# ----------------------------------------------------------------------
@router.post("/api/ts_point/scenario/reset")
@router.post("/api/ts_point/reconciliation/reset")
@router.post("/api/ts-point/scenario/reset")
@router.post("/api/ts/reconciliation/reset")
async def reset_scenario(request: Request) -> dict[str, Any]:
    data = await _payload(request)
    scenario = str(data.get("scenario") or data.get("state") or "conflict")
    return reconciliation_service.reset(scenario)
