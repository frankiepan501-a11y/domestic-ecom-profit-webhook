# domestic-ecom-profit

国内电商毛利报表 webhook 服务 — 接收任务台触发，自动跑 27 列毛利模型，生成飞书电子表格。

## 端点

| Method | Path | 用途 |
|---|---|---|
| GET | `/health` | 健康检查 |
| POST | `/profit/run` | 异步触发某 record_id (立即返回) |
| POST | `/profit/run-sync` | 同步触发 - 等结果 (本地测试用) |
| POST | `/profit/rerun-initial-check` | 只用运营已上传附件静默重跑初检；不生成报表、不发新卡 |
| GET | `/profit/poll` | 扫任务台找"🔥触发计算"行 |
| POST | `/profit/poll-and-run` | n8n cron 用 - 扫 + 触发 |
| GET | `/upload` | 国内电商卡片化资料工作台 |
| POST | `/upload` | 单项资料追加上传 |
| POST | `/upload/batch` | 文件夹批量上传并自动归类 |
| POST | `/upload/action` | 逐行确认无广告/无结算/补充说明 |
| POST | `/upload/submit` | 提交 P0 初检，缺口卡或试算 |
| POST | `/cards/invalidate-finance` | 将指定月份旧财务卡原位改成“本卡无效”，不补发新卡 |

所有 POST 端点需要 `Authorization: Bearer <WEBHOOK_BEARER_TOKEN>`.

`/upload*` 端点使用卡片链接里的 `run_id + token` 鉴权，不走 Bearer；运营只通过飞书卡片进入，不需要访问 Base 或任务台。

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| FEISHU_APP_ID | cli_a9f6ae86fce8dbd8 | 聪哥1号 |
| FEISHU_APP_SECRET | r0eQTiBoP1WnQCUnBanMQeu5ACT57at7 | |
| TASK_APP_TOKEN | IKyGb1jydaZW7msBzAicViiWngg | 任务台多维表 |
| TASK_TABLE_ID | tblMYHXRHZ0GaqMh | |
| FRANKIE_OPEN_ID | ou_629ce01f4bc31de078e10fcb038dbf78 | |
| OPS_CARD_CHAT_ID | oc_3240df569ced84c1541b6f7cd217d88f | 运营资料提交卡和缺口卡正式派送群；卡内实时 @ 当前国内平台运营专员 |
| OPS_CARD_CHAT_NAME | 国内电商平台沟通群 | 审计日志和接口回执中的群名 |
| OPS_CARD_FRANKIE_ONLY | false | `true` 时运营卡只发 Frankie 测试私聊；生产保持 `false` |
| COST_GAP_ALERT_FRANKIE_ONLY | false | 仅用于显式人工排查；日常生产保持 `false`，资料/成本缺口卡直接发国内电商群 |
| FINANCE_CONFIRM_CHAT_ID | oc_6b2da626d80eb6284bbe9dcf895030b9 | 财务确认卡正式派送群；Frankie-only 测试不走该群 |
| COMPANY_PROFIT_SERVICE_BASE_URL | https://finance-report-audit.zeabur.app | 四个平台确认后写公司毛利总表的服务地址 |
| COMPANY_PROFIT_SERVICE_TOKEN | 空 | 公司毛利总表服务鉴权；生产必须配置，未配置时禁止归档 |
| DOMESTIC_ECOM_REPORT_FOLDER_TOKEN | YmLtfYSA2lLIqBdEr6kcxCYLnvy | 国内电商毛利报表云盘归档文件夹：`毛利报表总目录 / 02 国内电商 / 国内电商毛利表` |
| DOMESTIC_ECOM_REPORT_FOLDER_PATH | 飞书云盘 / 毛利报表总目录 / 02 国内电商 / 国内电商毛利表 | 财务卡片和通知展示的云盘导航路径 |
| WEBHOOK_BEARER_TOKEN | ecom-profit-webhook-2026 | n8n 调用鉴权 |

报表云盘归档规范见 `docs/profit_report_drive_archive.md`。

## 国内电商毛利缺口责任口径

- 数据入口固定为国内运营上传的平台官方导出附件；AI只审核附件表头、识别内容并统计。除非另行明确授权改变数据源，否则不登录抖店或其他平台后台取数。

- 订单资料、订单匹配、采购成本、物流成本、平台资料缺口全部由国内电商运营统一补充或协调；采购不直接接收本报表缺口卡。
- 卡片统一发国内电商平台沟通群，并按当前职务实时 @ 国内平台运营专员；需要采购或物流提供信息时，由国内运营沟通并收口。
- 对象类型在卡内明确区分店铺、订单号、运单号、ERP SKU 和待核实对象，不能把订单号或运单号显示成 ERP SKU。
- 结算月包含跨月订单时，运营卡按结算文件中的最早下单月份提示订单明细补导范围；源文件没有下单日期时，保守回溯结算月前 3 个月并给出明确起止日期。
- 每条缺口明细都直接写平台/店铺，避免多平台同卡时把订单或 ERP SKU 归错店铺。
- 单路缺口超过 25 条时自动分页；卡片发布前做结构自检和发送后回读，不重复给 Frankie 私聊发送格式样卡。
- 群发送失败不记为已发送，后续可重试；防重复记录查询临时失败时，只跳过当前这一张卡并记录告警。
- 只有带输入、审批、回调或会改变业务状态的交互卡，才需要单独的人审测试；本缺口卡只有说明和上传页链接，自动自检通过后直接走国内电商群。

## v0.1 限制

- 只支持天猫 POWKONG 旗舰店 (其他店铺第二版加)
- SKU 成本 fallback 硬编码 5 个 (第二版接领星 API 自动同步)
- 物流支持顺丰 + 中通 (其他第二版加)
