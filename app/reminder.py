"""月初上传提醒 (3-5号窗口第一个工作日): 运营收操作指引, 财务收监督提醒。

v0.9 (2026-05-29 Frankie 定): n8n cron 每月3-5号09:00BJ每天调, 端点自判第一个工作日才发。
对象按职务实时查(岗位换人自动跟随)。提醒的是「上月」报表数据。
"""
from datetime import datetime
from . import config, feishu


def _prev_month() -> str:
    now = datetime.now()
    year = now.year if now.month > 1 else now.year - 1
    month = now.month - 1 or 12
    return f"{year}-{month:02d}"


def should_remind_today() -> bool:
    """3-5号窗口的第一个工作日才发 (避开周末; 每天cron调自判, 无需去重存储)。"""
    today = datetime.now()
    if today.day not in (3, 4, 5):
        return False
    if today.weekday() >= 5:  # 周六日
        return False
    for d in range(3, today.day):  # 3号到今天之间已有更早工作日 → 那天已发
        if today.replace(day=d).weekday() < 5:
            return False
    return True


async def monthly_upload_reminder(force: bool = False) -> dict:
    if not force and not should_remind_today():
        return {"skipped": "not first workday in 3-5", "day": datetime.now().day}
    ym = _prev_month()
    panel = config.TASK_PANEL_URL
    deadline = config.REMIND_DEADLINE_DAY

    ops_msg = (
        f"🟡 [FIN·P2] 国内电商毛利报表 · {ym} 数据上传提醒\n"
        f"请在本月 {deadline}前 完成 {ym} 各平台数据上传:\n"
        f"① 各店导出 4 类报表(订单/退款/平台费/广告), 命名 {{平台}}_{{店铺}}_{ym[:4]}年{int(ym[5:])}月{{类型}}\n"
        f"② 抖音/京东订单明细务必带「快递信息(运单号)」+「商家编码」\n"
        f"③ 上传到任务台对应行(状态自动从「待上传」推进)\n"
        f"④ 物流: 顺丰自动, 中通/其他承运商账单找蔡宗佑\n"
        f"⑤ 全部传齐后把「月度汇总行」状态改「🔥触发计算」→ 自动生成报表\n"
        f"任务台: {panel}"
    )
    fin_msg = (
        f"🟡 [FIN·P2] 国内电商毛利报表 · {ym} 监督提醒\n"
        f"请留意国内电商部 {ym} 数据上传进度:\n"
        f"① 监督数据是否齐全(10 店铺数据 + 物流账单)\n"
        f"② 督促国内电商部在本月 {deadline}前 完成上传 + 触发生成报表\n"
        f"任务台: {panel}"
    )

    sent: dict = {"ops": [], "finance": []}
    try:
        ops = await feishu.resolve_users_jt_fallback(config.REMIND_OPS_DEPT_ROOTS, config.REMIND_OPS_JOB_TITLES)
        for oid, name in ops.items():
            await feishu.send_text(oid, ops_msg)
            sent["ops"].append(name or oid)
    except Exception as e:
        sent["ops_error"] = str(e)
    try:
        fin = await feishu.resolve_users_jt_fallback(config.REMIND_FINANCE_DEPT_ROOTS, config.REMIND_FINANCE_JOB_TITLES)
        for oid, name in fin.items():
            await feishu.send_text(oid, fin_msg)
            sent["finance"].append(name or oid)
    except Exception as e:
        sent["finance_error"] = str(e)
    return {"year_month": ym, "sent": sent}


# ===== 逾期升级: 8-10 号还没生成报表 → 数据齐自动触发 / 否则 P1 催 =====
_ESCALATE_DAYS = (8, 9, 10)  # 截止 8 号; 8-10 号每天检查, 直到生成或窗口结束
_SHOP_ATT = ["订单明细", "退款明细", "平台费用", "广告/推广"]  # 店铺数据行附件字段


def _ftext(v) -> str:
    if isinstance(v, list) and v:
        return v[0].get("text", "") if isinstance(v[0], dict) else str(v[0])
    if isinstance(v, dict):
        return v.get("text") or v.get("value") or ""
    return v or ""


async def escalate_overdue(force: bool = False) -> dict:
    """8-10 号检查上月报表: ①数据齐+汇总行未触发→自动触发 ②还有缺/卡住/失败→P1 催运营+财务+Frankie。
    只读汇总行状态(✅已完成/计算中跳过) + 数据行附件齐否; 不依赖运营手动改状态。"""
    today = datetime.now()
    if not force and today.day not in _ESCALATE_DAYS:
        return {"skipped": "not in 8-10 window", "day": today.day}
    ym = _prev_month()
    panel = config.TASK_PANEL_URL
    rows = await feishu.bitable_search_records(config.TASK_APP_TOKEN, config.TASK_TABLE_ID)
    month_rows = [r for r in rows if _ftext(r.get("fields", {}).get("月份")) == ym]
    summary = next((r for r in month_rows
                    if r["fields"].get("数据类型") == "月度报表汇总"), None)
    if not summary:
        # 建行没跑(异常) — 直接催 Frankie 排查, 不擅自建行
        msg = (f"🟠 [FIN·P1] 国内电商毛利报表 · {ym} 异常\n"
               f"任务台未找到 {ym} 的月度汇总行(建行 cron 可能没跑), 请排查。\n任务台: {panel}")
        try:
            await feishu.send_text(config.FRANKIE_OPEN_ID, msg)
        except Exception:
            pass
        return {"action": "no_summary_row", "year_month": ym}

    status = _ftext(summary["fields"].get("任务状态"))
    if status in ("✅已完成", "计算中"):
        return {"skipped": "done_or_running", "status": status, "year_month": ym}

    # 数据行附件齐否(店铺数据看「订单明细」核心件, 物流账单看「物流月结账单」)
    missing = []
    for r in month_rows:
        f = r["fields"]
        dtype = f.get("数据类型")
        if dtype == "店铺数据":
            if not f.get("订单明细"):
                missing.append(_ftext(f.get("任务标题")))
        elif dtype == "物流账单":
            if not f.get("物流月结账单"):
                missing.append(_ftext(f.get("任务标题")))

    # 决策分支
    if not missing and status == "待上传":
        # 数据齐 + 没触发 → 自动触发(poll 1min 内生成)
        await feishu.bitable_update_record(
            config.TASK_APP_TOKEN, config.TASK_TABLE_ID, summary["record_id"],
            {"任务状态": "🔥触发计算"})
        msg = (f"🟠 [FIN·P1] 国内电商毛利报表 · {ym} 已自动触发生成\n"
               f"检测到 {ym} 数据已上传齐全, 但汇总行未触发 → 系统已自动改「🔥触发计算」, "
               f"约 1 分钟内生成报表并通知。\n任务台: {panel}")
        recipients = await _gather_recipients(ops=True, finance=False, frankie=True)
        sent = []
        for oid, name in recipients.items():
            try:
                await feishu.send_text(oid, msg)
                sent.append(name or oid)
            except Exception:
                pass
        return {"action": "auto_triggered", "year_month": ym, "notified": sent}

    # 还有缺 / 触发卡住 / 失败 → P1 催
    if missing:
        reason = f"还差 {len(missing)} 项数据未上传: {', '.join(missing[:12])}"
        cta = "请尽快上传缺失数据 → 全部传齐后系统会自动触发(也可手动改汇总行「🔥触发计算」)"
    elif status == "🔥触发计算":
        reason = "数据已齐且已触发, 但报表迟迟未生成(可能卡住)"
        cta = "请检查任务台汇总行状态, 或联系 Frankie 排查"
    elif status == "❌失败":
        reason = "上次生成失败(❌失败)"
        cta = "请检查数据格式后, 重新把汇总行改「🔥触发计算」重试"
    else:
        reason = f"汇总行状态={status or '空'}, 报表尚未生成"
        cta = "请把汇总行改「🔥触发计算」生成报表"

    ops_msg = (f"🟠 [FIN·P1] 国内电商毛利报表 · {ym} 逾期未生成(已过 {config.REMIND_DEADLINE_DAY})\n"
               f"{reason}\n{cta}\n任务台: {panel}")
    fin_msg = (f"🟠 [FIN·P1] 国内电商毛利报表 · {ym} 逾期未生成(已过 {config.REMIND_DEADLINE_DAY})\n"
               f"{reason}\n请督促国内电商部尽快补齐并生成报表。\n任务台: {panel}")
    frankie_msg = (f"🟠 [FIN·P1] 国内电商毛利报表 · {ym} 逾期未生成(已过 {config.REMIND_DEADLINE_DAY})\n"
                   f"{reason}\n汇总行状态={status or '空'}; 已催运营+财务。\n任务台: {panel}")

    sent = {"ops": [], "finance": [], "frankie": False}
    try:
        ops = await feishu.resolve_users_jt_fallback(config.REMIND_OPS_DEPT_ROOTS, config.REMIND_OPS_JOB_TITLES)
        for oid, name in ops.items():
            await feishu.send_text(oid, ops_msg)
            sent["ops"].append(name or oid)
    except Exception as e:
        sent["ops_error"] = str(e)
    try:
        fin = await feishu.resolve_users_jt_fallback(config.REMIND_FINANCE_DEPT_ROOTS, config.REMIND_FINANCE_JOB_TITLES)
        for oid, name in fin.items():
            await feishu.send_text(oid, fin_msg)
            sent["finance"].append(name or oid)
    except Exception as e:
        sent["finance_error"] = str(e)
    try:
        await feishu.send_text(config.FRANKIE_OPEN_ID, frankie_msg)
        sent["frankie"] = True
    except Exception:
        pass
    return {"action": "escalated", "year_month": ym, "status": status,
            "missing": missing, "sent": sent}


async def _gather_recipients(ops: bool, finance: bool, frankie: bool) -> dict:
    """合并运营/财务/Frankie 收件人 → {open_id: name}, 去重。"""
    out: dict = {}
    if ops:
        try:
            out.update(await feishu.resolve_users_jt_fallback(
                config.REMIND_OPS_DEPT_ROOTS, config.REMIND_OPS_JOB_TITLES))
        except Exception:
            pass
    if finance:
        try:
            out.update(await feishu.resolve_users_jt_fallback(
                config.REMIND_FINANCE_DEPT_ROOTS, config.REMIND_FINANCE_JOB_TITLES))
        except Exception:
            pass
    if frankie:
        out.setdefault(config.FRANKIE_OPEN_ID, "潘志聪")
    return out
