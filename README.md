# domestic-ecom-profit

国内电商毛利报表 webhook 服务 — 接收任务台触发，自动跑 27 列毛利模型，生成飞书电子表格。

## 端点

| Method | Path | 用途 |
|---|---|---|
| GET | `/health` | 健康检查 |
| POST | `/profit/run` | 异步触发某 record_id (立即返回) |
| POST | `/profit/run-sync` | 同步触发 - 等结果 (本地测试用) |
| GET | `/profit/poll` | 扫任务台找"🔥触发计算"行 |
| POST | `/profit/poll-and-run` | n8n cron 用 - 扫 + 触发 |

所有 POST 端点需要 `Authorization: Bearer <WEBHOOK_BEARER_TOKEN>`.

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| FEISHU_APP_ID | cli_a9f6ae86fce8dbd8 | 聪哥1号 |
| FEISHU_APP_SECRET | r0eQTiBoP1WnQCUnBanMQeu5ACT57at7 | |
| TASK_APP_TOKEN | IKyGb1jydaZW7msBzAicViiWngg | 任务台多维表 |
| TASK_TABLE_ID | tblMYHXRHZ0GaqMh | |
| FRANKIE_OPEN_ID | ou_629ce01f4bc31de078e10fcb038dbf78 | |
| WEBHOOK_BEARER_TOKEN | ecom-profit-webhook-2026 | n8n 调用鉴权 |

## v0.1 限制

- 只支持天猫 POWKONG 旗舰店 (其他店铺第二版加)
- SKU 成本 fallback 硬编码 5 个 (第二版接领星 API 自动同步)
- 物流支持顺丰 + 中通 (其他第二版加)
