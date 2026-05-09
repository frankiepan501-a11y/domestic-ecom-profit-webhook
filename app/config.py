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
