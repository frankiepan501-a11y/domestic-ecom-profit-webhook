"""FastAPI 入口 — 国内电商毛利报表 webhook 服务."""
import asyncio
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
from . import config, task_runner, feishu

app = FastAPI(title="domestic-ecom-profit", version="0.2.0")


class RunRequest(BaseModel):
    record_id: str


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.2.0", "task_app": config.TASK_APP_TOKEN}


def _check_auth(authorization: str | None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer")
    if authorization[7:] != config.WEBHOOK_BEARER_TOKEN:
        raise HTTPException(401, "invalid bearer")


@app.post("/profit/run")
async def run_profit(req: RunRequest, authorization: str | None = Header(None)):
    """触发某个汇总行的毛利报表生成 (异步, 立即返回)."""
    _check_auth(authorization)
    asyncio.create_task(task_runner.run_profit(req.record_id))
    return {"status": "started", "record_id": req.record_id}


@app.post("/profit/run-sync")
async def run_profit_sync(req: RunRequest, authorization: str | None = Header(None)):
    """同步触发 - 等待结果返回 (用于本地测试)."""
    _check_auth(authorization)
    res = await task_runner.run_profit(req.record_id)
    return res


@app.get("/profit/poll")
async def poll_pending(authorization: str | None = Header(None)):
    """n8n cron 调用此接口扫任务台 → 返回所有"🔥触发计算"行 → n8n 按行调 /profit/run."""
    _check_auth(authorization)
    records = await feishu.bitable_search_records(
        config.TASK_APP_TOKEN, config.TASK_TABLE_ID)
    pending = []
    for r in records:
        f = r.get("fields", {})
        if f.get("数据类型") == "月度报表汇总" and f.get("任务状态") == "🔥触发计算":
            title = f.get("任务标题")
            if isinstance(title, list) and title:
                title = title[0].get("text", "")
            pending.append({"record_id": r["record_id"], "title": title})
    return {"pending": pending, "count": len(pending)}


@app.post("/profit/poll-and-run")
async def poll_and_run(authorization: str | None = Header(None)):
    """n8n cron 一键调用 - 扫 + 触发所有 pending 行."""
    _check_auth(authorization)
    records = await feishu.bitable_search_records(
        config.TASK_APP_TOKEN, config.TASK_TABLE_ID)
    triggered = []
    for r in records:
        f = r.get("fields", {})
        if f.get("数据类型") == "月度报表汇总" and f.get("任务状态") == "🔥触发计算":
            asyncio.create_task(task_runner.run_profit(r["record_id"]))
            triggered.append(r["record_id"])
    return {"triggered": triggered, "count": len(triggered)}


@app.post("/tasks/ensure-month")
async def ensure_month(year_month: str | None = None, authorization: str | None = Header(None)):
    """每月初建当月任务台行 (12行, 幂等)。n8n cron 每月1号调用; year_month 省略=当月。"""
    _check_auth(authorization)
    from . import task_seeder
    return await task_seeder.ensure_month_rows(year_month)


@app.post("/tasks/remind-monthly")
async def remind_monthly(force: bool = False, authorization: str | None = Header(None)):
    """月初上传提醒 (3-5号窗口第一个工作日才真发)。n8n cron 每月3-5号每天调; force=true 强制发(测试)。"""
    _check_auth(authorization)
    from . import reminder
    return await reminder.monthly_upload_reminder(force=force)
