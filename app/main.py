"""FastAPI 入口 — 国内电商毛利报表 webhook 服务."""
import asyncio
from fastapi import FastAPI, Header, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from . import config, task_runner, feishu

app = FastAPI(title="domestic-ecom-profit", version="0.3.4")


class RunRequest(BaseModel):
    record_id: str


@app.get("/health")
async def health():
    return {"status": "ok", "version": "0.3.4", "task_app": config.TASK_APP_TOKEN,
            "ledger_run_table": config.LEDGER_RUN_TABLE_ID,
            "cost_gap_responsibility_routing": True,
            "report_create_error_detail": True,
            "tmall_item_export_support": True,
            "cost_gap_alert_frankie_only": config.COST_GAP_ALERT_FRANKIE_ONLY}


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
    if config.CARD_WORKFLOW_ENABLED:
        from . import card_workflow
        return await card_workflow.send_monthly_intake(
            force=force,
            frankie_only=config.OPS_CARD_FRANKIE_ONLY,
        )
    from . import reminder
    return await reminder.monthly_upload_reminder(force=force)


@app.post("/tasks/escalate-overdue")
async def escalate_overdue(force: bool = False, authorization: str | None = Header(None)):
    """逾期升级 (8-10号窗口): 上月数据齐+汇总行未触发→自动触发; 否则 P1 催运营+财务+Frankie。
    n8n cron 每月8-10号每天调; force=true 强制跑(测试)。"""
    _check_auth(authorization)
    from . import reminder
    return await reminder.escalate_overdue(force=force)


@app.post("/cards/monthly-intake")
async def cards_monthly_intake(year_month: str | None = None, force: bool = False,
                               dry_run: bool = False, frankie_only: bool = False,
                               authorization: str | None = Header(None)):
    """创建/复用 run_id, 写资料清单 ledger, 发运营资料提交卡。"""
    _check_auth(authorization)
    from . import card_workflow
    return await card_workflow.send_monthly_intake(
        year_month, force=force, dry_run=dry_run, frankie_only=frankie_only)


@app.post("/cards/test")
async def cards_test(year_month: str | None = None, send: bool = False,
                     authorization: str | None = Header(None)):
    """P0 smoke: 默认只返回卡片 JSON; send=true 发 Frankie 私聊。"""
    _check_auth(authorization)
    from . import card_workflow
    return await card_workflow.send_monthly_intake(
        year_month, force=True, dry_run=not send, frankie_only=True)


@app.post("/cards/test-samples")
async def cards_test_samples(year_month: str | None = None, send: bool = False,
                             authorization: str | None = Header(None)):
    """P0 smoke: return or send ops/gap/finance sample cards to Frankie."""
    _check_auth(authorization)
    from . import card_workflow
    return await card_workflow.send_sample_cards(year_month, send=send)


@app.post("/cards/finance-confirm")
async def cards_finance_confirm(year_month: str | None = None,
                                workbook_url: str = "",
                                dry_run: bool = False,
                                frankie_only: bool = True,
                                authorization: str | None = Header(None)):
    """Send platform-level monthly gross-profit confirmation cards. Defaults to Frankie-only smoke."""
    _check_auth(authorization)
    from . import card_workflow
    return await card_workflow.send_finance_confirm_for_month(
        year_month,
        workbook_url=workbook_url,
        dry_run=dry_run,
        frankie_only=frankie_only,
    )


@app.post("/cards/callback")
async def cards_callback(req: dict, authorization: str | None = Header(None)):
    """Event Hub 转发 card.action.trigger 到这里。业务写入和 PATCH 在服务端幂等处理。"""
    _check_auth(authorization)
    from . import card_workflow
    return await card_workflow.handle_callback(req)


@app.get("/upload", response_class=HTMLResponse)
async def upload_page(run_id: str, token: str):
    from . import card_workflow
    return HTMLResponse(await card_workflow.upload_page(run_id, token))


@app.post("/upload", response_class=HTMLResponse)
async def upload_files(run_id: str = Form(...), token: str = Form(...),
                       file_manifest_id: str = Form(...),
                       files: list[UploadFile] = File(...)):
    from . import card_workflow
    result = await card_workflow.handle_upload(run_id, token, file_manifest_id, files)
    if not result.get("ok"):
        msg = html_escape(str(result.get("error", "upload failed")))
        return HTMLResponse(upload_result_html("上传失败", run_id, token, f"<p>{msg}</p>"), status_code=400)
    count = len(result.get("uploaded") or [])
    total = len(result.get("attachments") or [])
    return HTMLResponse(upload_result_html(
        "上传成功",
        run_id,
        token,
        f"<p>本次上传 {count} 个文件；该资料项当前共保留 {total} 个附件。系统已写入资料清单附件台并镜像到旧任务台。</p>",
    ))


@app.post("/upload/batch", response_class=HTMLResponse)
async def upload_batch(run_id: str = Form(...), token: str = Form(...),
                       files: list[UploadFile] = File(...)):
    from . import card_workflow
    result = await card_workflow.handle_batch_upload(run_id, token, files)
    if not result.get("ok"):
        msg = html_escape(str(result.get("error", "upload failed")))
        return HTMLResponse(upload_result_html("文件夹上传失败", run_id, token, f"<p>{msg}</p>"), status_code=400)
    groups = result.get("uploaded_groups") or []
    unmatched = result.get("unmatched") or []
    ambiguous = result.get("ambiguous") or []
    lines = [f"<p>已自动归类 {len(groups)} 个资料项。</p>"]
    if groups:
        lines.append("<ul>")
        for g in groups:
            label = html_escape(str(g.get("label", "")))
            names = "，".join(html_escape(str(x)) for x in (g.get("uploaded") or []))
            lines.append(f"<li><b>{label}</b>：{names}</li>")
        lines.append("</ul>")
    if unmatched:
        lines.append("<p class=\"danger\"><b>未识别文件</b>：请改名或用单项上传补充。</p><ul>")
        lines.extend(f"<li>{html_escape(str(x))}</li>" for x in unmatched)
        lines.append("</ul>")
    if ambiguous:
        lines.append("<p class=\"danger\"><b>匹配不唯一文件</b>：请把平台/店铺/文件类型写进文件夹或文件名。</p><ul>")
        lines.extend(f"<li>{html_escape(str(x))}</li>" for x in ambiguous)
        lines.append("</ul>")
    return HTMLResponse(upload_result_html("文件夹上传完成", run_id, token, "".join(lines)))


@app.post("/upload/action", response_class=HTMLResponse)
async def upload_action(run_id: str = Form(...), token: str = Form(...),
                        file_manifest_id: str = Form(...),
                        action_name: str = Form(...), note: str = Form("")):
    from . import card_workflow
    result = await card_workflow.handle_manifest_action(run_id, token, file_manifest_id, action_name, note)
    if not result.get("ok"):
        msg = html_escape(str(result.get("error", "action failed")))
        return HTMLResponse(upload_result_html("操作失败", run_id, token, f"<p>{msg}</p>"), status_code=400)
    return HTMLResponse(upload_result_html("操作成功", run_id, token, f"<p>{html_escape(str(result.get('message', '已处理')))}</p>"))


@app.post("/upload/submit", response_class=HTMLResponse)
async def upload_submit(run_id: str = Form(...), token: str = Form(...)):
    from . import card_workflow
    result = await card_workflow.submit_upload_gate(run_id, token)
    if not result.get("ok"):
        msg = html_escape(str(result.get("error", "submit failed")))
        return HTMLResponse(upload_result_html("提交初检失败", run_id, token, f"<p>{msg}</p>"), status_code=400)
    gate = result.get("gate") or {}
    if gate.get("ready"):
        body = "<p>资料已通过 P0 gate，系统已进入试算链路。试算完成后会发财务确认卡。</p>"
    else:
        body = (
            f"<p>资料已提交初检，当前 P0 缺口数：<b>{html_escape(str(gate.get('open_p0', 0)))}</b>。</p>"
            "<p>系统会把 P0 缺口卡发到国内电商平台沟通群，并 @ 当前运营负责人。</p>"
        )
    return HTMLResponse(upload_result_html("已提交初检", run_id, token, body))


def html_escape(text: str) -> str:
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))


def upload_result_html(title: str, run_id: str, token: str, body: str) -> str:
    safe_title = html_escape(title)
    back = f"/upload?run_id={html_escape(run_id)}&token={html_escape(token)}"
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>{safe_title}</title>
<style>
body {{ font-family:"Microsoft YaHei", "Segoe UI", sans-serif; color:#172033; max-width:860px; margin:42px auto; line-height:1.6; padding:0 20px; }}
a, button {{ color:#1d5cff; }}
.box {{ border:1px solid #d8dee8; border-radius:8px; padding:18px; background:#f7f9fc; }}
.danger {{ color:#b42318; }}
</style></head>
<body><div class="box">
<h2>{safe_title}</h2>
{body}
<p><a href="{back}">返回资料工作台</a></p>
</div></body></html>"""
