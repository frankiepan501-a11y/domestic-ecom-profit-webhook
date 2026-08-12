"""Handoff to the company gross-profit ledger after domestic platform approvals."""
from __future__ import annotations

import httpx

from . import config


def _verified_aggregate_result(poll_payload: dict) -> dict:
    """Treat the company run ledger as the final proof, not the callback HTTP 200."""
    run_state = poll_payload.get("run") or {}
    archived = (
        run_state.get("报表状态") == "已归档"
        and run_state.get("总表状态") == "已灌总表"
    )
    return {
        "ok": archived,
        "archived": archived,
        "report_status": run_state.get("报表状态"),
        "total_status": run_state.get("总表状态"),
    }


async def finalize_domestic(period: str, idempotency_key: str) -> dict:
    if not config.COMPANY_PROFIT_SERVICE_BASE_URL or not config.COMPANY_PROFIT_SERVICE_TOKEN:
        return {"ok": False, "reason": "公司毛利汇总服务路由或鉴权未配置"}
    headers = {"Authorization": f"Bearer {config.COMPANY_PROFIT_SERVICE_TOKEN}"}
    params = {
        "period": period,
        "platform": "domestic_ecom",
        "recipient_mode": "frankie",
        "send": "false",
        "include_cards": "false",
        "generate": "false",
    }
    async with httpx.AsyncClient(timeout=120) as client:
        seeded = await client.post(
            f"{config.COMPANY_PROFIT_SERVICE_BASE_URL.rstrip('/')}/profit-workflow/run-month",
            params=params,
            headers=headers,
        )
        seeded.raise_for_status()
        seed_payload = seeded.json()
        results = seed_payload.get("results") or []
        if not results:
            return {"ok": False, "reason": "公司毛利运行台未返回国内电商批次"}
        company_run = results[0]
        if company_run.get("status") != "finance_ready":
            return {
                "ok": False,
                "reason": company_run.get("message") or f"公司初审状态={company_run.get('status')}",
                "company_run": company_run,
            }
        callback_body = {
            "event": {
                "action": {"value": {
                    "action": "company_profit_finance_approve",
                    "run_id": company_run.get("run_id"),
                    "idempotency_key": idempotency_key,
                    "card_type": "domestic_platform_approval_handoff",
                }}
            }
        }
        finalized = await client.post(
            f"{config.COMPANY_PROFIT_SERVICE_BASE_URL.rstrip('/')}/profit-workflow/callback",
            json=callback_body,
            headers=headers,
        )
        finalized.raise_for_status()
        payload = finalized.json()
        polled = await client.get(
            f"{config.COMPANY_PROFIT_SERVICE_BASE_URL.rstrip('/')}/profit-workflow/poll-run",
            params={"run_id": company_run.get("run_id")},
            headers=headers,
        )
        polled.raise_for_status()
        poll_payload = polled.json()
        aggregate_result = _verified_aggregate_result(poll_payload)
        archived = aggregate_result["archived"]
        return {
            "ok": bool(payload.get("ok")) and archived,
            "reason": "" if payload.get("ok") and archived else "公司毛利汇总未进入‘已灌总表/已归档’终态",
            "company_run_id": company_run.get("run_id"),
            "aggregate_result": aggregate_result,
            "callback": payload,
            "poll": poll_payload,
        }
