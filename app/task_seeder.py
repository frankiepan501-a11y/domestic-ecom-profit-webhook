"""每月初自动建当月任务台行 (12行: 10店铺数据 + 1物流账单 + 1月度汇总)，幂等。

v0.8 (2026-05-29): 省掉每月人工建行。n8n cron 每月1号调 /tasks/ensure-month。
第2步上传数据仍需运营 (数据源在各平台后台手工导)。
"""
from datetime import datetime
from . import config, feishu

# 责任人 open_id (聪哥1号 namespace, 见 memory/reference_feishu_users.md)
_OWNER = {
    "赵伟俊": "ou_274ee5199a763b7ec97980cd54e3fecb",
    "蔡宗佑": "ou_6a3a692528a5606e1c27e1084053e551",
    "潘志聪": "ou_629ce01f4bc31de078e10fcb038dbf78",
}

# 每月固定 12 行模板: (任务标题后缀, 数据类型, 平台, 店铺, 责任人)
_TEMPLATE = [
    ("天猫POWKONG旗舰店", "店铺数据", "天猫", "POWKONG旗舰店", "赵伟俊"),
    ("天猫纷岚店", "店铺数据", "天猫", "纷岚店", "赵伟俊"),
    ("抖音纷岚店", "店铺数据", "抖音", "纷岚店", "赵伟俊"),
    ("抖音宝空店", "店铺数据", "抖音", "宝空店", "赵伟俊"),
    ("小红书纷岚店", "店铺数据", "小红书", "纷岚店", "赵伟俊"),
    ("小红书宝空店", "店铺数据", "小红书", "宝空店", "赵伟俊"),
    ("拼多多正方体电玩店", "店铺数据", "拼多多", "正方体电玩店", "蔡宗佑"),
    ("淘宝正方体电玩店", "店铺数据", "淘宝", "正方体电玩店", "蔡宗佑"),
    ("京东纷岚店", "店铺数据", "京东", "京东纷岚店", "赵伟俊"),
    ("京东宝空店", "店铺数据", "京东", "宝空店", "赵伟俊"),
    ("物流月结账单(全公司)", "物流账单", None, None, "蔡宗佑"),
    ("月度毛利报表汇总(触发器)", "月度报表汇总", None, None, "潘志聪"),
]


def _month_of(fields: dict) -> str:
    m = fields.get("月份")
    if isinstance(m, list) and m:
        return m[0].get("text", "")
    return m or ""


async def ensure_month_rows(year_month: str | None = None) -> dict:
    """幂等建当月12行。该月已有任意行则跳过 (不重复建)。"""
    if not year_month:
        year_month = datetime.now().strftime("%Y-%m")

    existing = await feishu.bitable_search_records(config.TASK_APP_TOKEN, config.TASK_TABLE_ID)
    has = [r for r in existing if _month_of(r.get("fields", {})) == year_month]
    if has:
        return {"skipped": True, "year_month": year_month, "existing": len(has)}

    records = []
    for suffix, dtype, platform, shop, owner in _TEMPLATE:
        fields = {
            "任务标题": f"{year_month} {suffix}",
            "数据类型": dtype,
            "月份": year_month,
            "任务状态": "待上传",
            "责任人": [{"id": _OWNER[owner]}],
        }
        if platform:
            fields["平台"] = platform
        if shop:
            fields["店铺"] = shop
        records.append({"fields": fields})

    res = await feishu.bitable_batch_create(config.TASK_APP_TOKEN, config.TASK_TABLE_ID, records)
    code = res.get("code")
    created = len((res.get("data") or {}).get("records") or []) if code == 0 else 0
    return {"skipped": False, "year_month": year_month, "created": created,
            "code": code, "msg": res.get("msg")}
