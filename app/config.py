"""配置 — 全部从环境变量读，部署到 Zeabur 时通过 env 注入。"""
import os

# 飞书 - 聪哥1号 (sheets/bitable/im 主账号)
FEISHU_APP_ID = os.getenv("FEISHU_APP_ID", "cli_a9f6ae86fce8dbd8")
FEISHU_APP_SECRET = os.getenv("FEISHU_APP_SECRET", "r0eQTiBoP1WnQCUnBanMQeu5ACT57at7")

# 任务台多维表格
TASK_APP_TOKEN = os.getenv("TASK_APP_TOKEN", "IKyGb1jydaZW7msBzAicViiWngg")
TASK_TABLE_ID = os.getenv("TASK_TABLE_ID", "tblMYHXRHZ0GaqMh")

# 任务台字段 ID
FIELD_IDS = {
    "任务标题": "fldrvBum5E",
    "数据类型": "fld9KkWVOJ",
    "计算开始时间": "fldF8l5hNP",
    "订单明细": "fld5UzrBms",
    "月份": "fld0Noia8u",
    "平台": "fldv5qXeDk",
    "店铺": "flduXJJBrO",
    "快递公司": "fldkI09Cj2",
    "退款明细": "fldVtVF7Zm",
    "平台费用": "flduZcLPbV",
    "广告/推广": "fldwJWSDo2",
    "物流月结账单": "fldXhvQxZ6",
    "任务状态": "fldTME77e4",
    "报表飞书链接": "fldgouAJWx",
    "计算完成时间": "fldsitQcCe",
    "错误日志": "fldTaeVgFe",
    "责任人": "fldgVZzuvd",
    "备注": "fldpTpYBSH",
}

# Frankie open_id (聪哥1号 namespace) — 用于推送报表生成完成消息
FRANKIE_OPEN_ID = os.getenv("FRANKIE_OPEN_ID", "ou_629ce01f4bc31de078e10fcb038dbf78")

# 领星 ERP
LINGXING_APP_ID = os.getenv("LINGXING_APP_ID", "ak_B1P0qz2mkImfS")
LINGXING_APP_SECRET = os.getenv("LINGXING_APP_SECRET", "IMJm0f/dwDM7YYR+2FrlEQ==")

# 服务鉴权 (n8n 调用时带 Bearer)
WEBHOOK_BEARER_TOKEN = os.getenv("WEBHOOK_BEARER_TOKEN", "ecom-profit-webhook-2026")

# 顺丰丰桥 API (v0.5 加, 2026-05-12)
SF_PARTNER_ID = os.getenv("SF_PARTNER_ID", "ADEDZLPVZYMO")
SF_CHECKWORD = os.getenv("SF_CHECKWORD", "Iwvsg9CTcpRcoSUK85uIpYsBjaR7jngY")
SF_ENV = os.getenv("SF_ENV", "prod")  # prod / sandbox
SF_API_ENABLED = os.getenv("SF_API_ENABLED", "true").lower() == "true"

# ===== 月度报表自动授权 (2026-05-22 Frankie 定) =====
# 需求: 每月新报表自动给 我/吴晓丹 + 财务/采购/物流仓储/电商运营(含子部门) 全员权限。
# 飞书坑: tenant token 无法把"部门"直接加成 sheet 协作者(返回 1063001 Invalid parameter),
#         故运行时实时解析部门(含子部门)当前成员 → 逐人授权。每月重跑自动同步新入职/转岗/新子部门。
# 部门 open_department_id (聪哥1号实测拉取, 2026-05-22):
REPORT_GRANT_DEPT_ROOTS = [
    "od-ad59abe171a6b0a419a5e3969fb349ad",  # 财务部
    "od-273719791eed9b0558c20e0960da991a",  # 采购部
    "od-5f04ee41728635fa2a3f595644e8d83f",  # 物流仓储部
    "od-9442cd1e71b6c1a3a42f503d6f4c4940",  # 电商运营部 (运行时展开含 站外/跨境/国内电商 子部门)
]
# 显式个人授权, 覆盖部门默认的 view: (open_id, perm). perm ∈ view/edit/full_access
REPORT_GRANT_USERS = [
    (FRANKIE_OPEN_ID, "full_access"),                 # 潘志聪 (Frankie)
    ("ou_c65fc5c31c650790db623640b7ac74f7", "edit"),  # 吴晓丹 (COO)
]
# 部门成员默认权限
REPORT_GRANT_DEPT_PERM = "view"
