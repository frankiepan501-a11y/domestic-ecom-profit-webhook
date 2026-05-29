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
