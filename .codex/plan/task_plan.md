# Task Plan: 2026-07 国内电商毛利 P0 整改与静默初检

## Goal

把国内电商毛利工作流恢复为“运营上传官方导出附件 → AI识别/初检/计算 → 原卡闭环 → 无 P0 才给财务终审”，并完成 2026-07 生产止血。

## Scope

- In scope: 附件识别、缺口台同步、原缺口卡更新、旧卡停用、财务卡 P0 硬闸、公司总毛利汇总后归档、生产部署与只读回核。
- Out of scope: AI 登录抖店取数、代运营下载资料、补写业务数据、生成新的 7 月正式报表。
- Systems/data touched: 国内毛利代码库、Zeabur 国内毛利服务、飞书运行台/缺口台/审计台、既有飞书卡片。
- Risk level: high

## Confirmed Requirements

| Requirement | Source | Status |
|---|---|---|
| 数据源固定为运营上传的平台官方导出附件，AI不登录抖店 | user correction | implemented |
| 16 张旧财务卡全部标记“本卡无效，请勿操作” | user confirmed | completed: 16/16 |
| 一个月×渠道×平台只允许一个有效报表版本；旧按钮拒绝 | screenshot P0 + user | implemented |
| 有 P0 时禁止发/点财务终审卡 | screenshot P0 + user | implemented |
| 财务通过后先灌公司总毛利表，成功才归档 | screenshot P0 + user | implemented |
| 使用现有附件静默重跑，只更新原缺口卡，不新增重复卡 | user confirmed | completed |
| 宝空结算资料不足时只给赵伟俊一条精确补件说明 | user confirmed | completed |

## Acceptance Criteria

- [x] 57 项自动测试、编译检查、差异检查通过。
- [x] 生产运行提交 `16608f5`，健康检查 v0.4.0。
- [x] 16 张旧财务卡原位停用，失败 0；当前四个平台发送槽位已清空。
- [x] 现有附件静默重跑成功；新报表 0、新卡 0。
- [x] 8 张历史缺口卡收敛为 1 张有效 + 7 张无效；缺失 0、失败 0。
- [x] 当前 P0=2：抖音宝空结算表头不足、小红书纷岚一单缺快递单号。
- [x] 原有效卡真实 @ 赵伟俊，包含宝空缺少的 6 个字段和“不要重复上传涉税报送明细”。
- [x] 公司总表鉴权变量单变量新增，现有环境变量 58→59，无全量覆盖。

## Current State

- Current phase: Handoff（已完成）
- Last completed action: 回读有效原卡 `om_x100b68f5d05d24b0c34011561680f50`，确认内容、@、更新状态和无新增发送均通过。
- Next concrete action: 等赵伟俊补 ①抖音宝空订单级结算明细 ②小红书纷岚订单 `P799684994323319861` 的快递单号，再由运营点击“提交初检”。

## Evidence

| Evidence | Result |
|---|---|
| Zeabur deployment | `16608f53...` RUNNING |
| `/cards/invalidate-finance` | invalidated=16, failed=0 |
| `/profit/rerun-initial-check` | ok=true, open_p0=2, created_report=false, sent_new_card=false |
| `cost_gap_card_refresh` audit | patched, active=1, invalidated=7, missing=0, failed=0 |
| 发卡审计 | refresh 后 `cost_gap_alert_v2` 新增数=0 |
