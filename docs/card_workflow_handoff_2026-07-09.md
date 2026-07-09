# 国内电商毛利报表卡片化工作流交接

日期：2026-07-09

## 背景

旧流程依赖运营和财务进入 Base 任务台手动改状态。问题不是单次操作慢，而是每月反复出现“催资料、判缺口、等确认、追口径”的交接断点。新流程把 Base 降级为系统 ledger，业务人员只通过飞书卡片和上传页完成动作。

## 本次改动

- 新增 `app/ledger.py`：封装报表运行台、资料清单附件台、缺口例外台、输出报表台、审计日志台的读写。
- 新增 `app/cards.py`：运营资料提交卡、P0 缺口卡、财务确认卡、已处理结果卡。
- 新增 `app/card_workflow.py`：月度 run 创建、资料清单生成、P0 gate、试算触发、财务确认、callback 幂等与 PATCH。
- 更新 `app/main.py`：新增 `/cards/monthly-intake`、`/cards/test`、`/cards/test-samples`、`/cards/callback`、`/upload`；`/tasks/remind-monthly` 在 `CARD_WORKFLOW_ENABLED=true` 时发卡。
- 更新 `app/feishu.py`：支持 App3 发卡/PATCH、Base 创建记录、上传附件、open_id -> union_id。
- 更新 `app/writer.py`：输出 workbook 增加 `产品毛利_月度`、`产品毛利_季度` 两个 gate sheet。
- 更新 `app/task_seeder.py`：旧任务台建月行改为按月份过滤，避免全表扫描。

## 生产资源

服务：`domestic-ecom-profit.zeabur.app`

最新提交：

- `6c8e4d5` Add domestic profit card workflow ledger
- `9c08177` Limit domestic card workflow Base scans
- `584f9eb` Optimize domestic ledger lookups
- `c99b8f6` Add domestic card sample smoke endpoint

Ledger Base：`IKyGb1jydaZW7msBzAicViiWngg`

表：

- `报表运行台`：`tblIGDjnDtoceL6F`
- `资料清单附件台`：`tbla1F0KMEFqct60`
- `缺口例外台`：`tblqTPPHLWRhHkuD`
- `输出报表台`：`tblnMy4Jb8jK7JQz`
- `审计日志台`：`tblVggmv7oeMBMaj`

生产开关：

- `CARD_WORKFLOW_ENABLED=true`
- `CARD_WORKFLOW_FRANKIE_ONLY=true` 测试期只发 Frankie；Frankie 确认卡片流程后才改为 `false` 派给运营/财务。
- `FEISHU_EVENT_APP_ID` / `FEISHU_EVENT_APP_SECRET` 用于 App3 发卡和 PATCH。

## 测试纪律

卡片工作流上线或改派送对象时，先进入 Frankie-only 测试模式。禁止在 Frankie 完整走通“收到卡片 -> 上传/点击 -> callback 写 ledger -> 原卡 PATCH”的链路前，把 `/tasks/remind-monthly` 或等价生产入口直接发给运营负责人。

如果需要验证运营卡样式，使用 `/cards/test?send=true` 或 `/cards/test-samples?send=true` 发给 Frankie；不要用 `/tasks/remind-monthly?force=true` 直接打运营，除非 `CARD_WORKFLOW_FRANKIE_ONLY=true` 已生效。

## Callback 路由

n8n Event Hub `YjTXaoWAcy89xZpT` 新增 `domestic_profit_*` namespace：

`card.action.trigger -> Is Domestic Profit Action -> Domestic Profit Callback -> POST /cards/callback`

服务端以 `value.action` 路由，使用 `idempotency_key` 查审计日志，重复点击只 PATCH 原卡为已处理态，不重复写 Base、不重复触发试算。

## 验证记录

- `/health` 返回 `version=0.3.0`。
- `/cards/test?year_month=2026-06&send=false` dry-run 成功，生成 `domestic-ecom-profit-2026-06`，资料 checklist 41 项；优化后耗时约 8.4 秒。
- 资料清单附件台已写入 41 条 2026-06 manifest。
- `/cards/test?year_month=2026-06&send=true` 给 Frankie 发送运营资料提交卡成功。
- `/cards/callback` 对运营卡 `domestic_profit_ops_note` 返回 `patched_original_card=true`。
- 固定 `idempotency_key=smoke-idem-20260709-001` 连续回调两次，第二次返回 `duplicate=true` 且 PATCH 成功。
- `/cards/test-samples?year_month=2026-06&send=true` 给 Frankie 发送运营资料卡、P0 缺口卡、财务确认卡三类样例成功。
- P0 缺口样例卡和财务样例卡的 callback 均返回 `patched_original_card=true`。
- `CARD_WORKFLOW_ENABLED=true` 后，`/tasks/remind-monthly?force=true` 已走卡片路径并向运营负责人发出资料提交卡。

## 剩余风险和下一步

P0 已可用，但还没有把“附件上传后自动 parser 初检”的全部平台细分做到最优。当前 P0 gate 先覆盖资料是否提交、广告 0 确认、物流账单、无结算确认，后续要继续接平台级字段校验。

建议下一步：

- P0：把上传页附件按平台 parser 预检，失败写缺口例外台并发 P0 缺口卡。
- P1：将 manifest 创建从逐条 create 优化为 batch_create，降低首次新月份发卡耗时。
- P1：把财务确认后的汇总索引/归档写入做成独立可回放端点。
- P2：将 `/cards/test-samples` 保留为 smoke，但在生产月结后隐藏或限制为 Frankie only。
