# Task Plan: 国内电商毛利缺口统一归运营

## Goal

把 2026-07 遗漏的天猫物流与抖音资料缺口补发给赵伟俊，并让后续订单、采购成本、物流成本、平台资料缺口统一由国内电商运营闭环。

## Scope

- In scope: 飞书群补充通知、缺口分流逻辑、运营卡片文案、自动触发类别、测试、生产发布与验证。
- Out of scope: 修改本期报表金额、替运营补上传文件、改采购成本或物流成本数据。
- Systems/data touched: 国内毛利代码库、飞书国内电商群、飞书审计台账、Zeabur 服务。
- Risk level: high

## Clarified Requirements

| Requirement | Source | Status |
|---|---|---|
| 订单、采购成本、物流成本问题均由国内电商运营补充 | user confirmed | confirmed |
| 补发天猫物流成本、抖音宝空/纷岚资料缺口给赵伟俊 | user confirmed | sent and verified |
| 后续缺口卡统一发国内电商群并按岗位 @ | prior user confirmed | implemented |
| 无审批/无回调的信息卡不再反复发 Frankie 私聊样卡 | user corrected | implemented |

## Open Questions

| Question | Why it matters | Blocking? | Answer |
|---|---|---|---|
| 无 | 用户已明确生产发送与责任口径 | no | - |

## Assumptions

| Assumption | Confidence | How to verify |
|---|---|---|
| 当前岗位人员仍为赵伟俊 | high | 发送前实时按职务解析并核对姓名 |
| 报表缺口清单是本次缺口数量的准确信息源 | high | 只读读取 2026-07 新报表缺口清单 |

## Acceptance Criteria

- [x] 群补充卡包含天猫 105 行/89 个对象及抖音两店缺口，并真实 @ 赵伟俊。
- [x] 4 类国内毛利 P0 缺口全部进入 operations，procurement 保持为空。
- [x] 全量测试通过。
- [ ] 生产版本与提交一致；健康检查通过。
- [x] 项目修复记录、计划与候选教训完成。

## Phases

| Phase | Status | Verification |
|---|---|---|
| 1. Discovery | completed | 报表只读统计 + 原卡核对 |
| 2. Plan | completed | 用户责任口径已明确 |
| 3. Execute | completed | 补充卡已发；代码和文档已修改 |
| 4. Verify | in_progress | 测试与回放通过；待发布和健康检查 |
| 5. Handoff | pending | Done/todos/next step |

## Current State

- Current phase: 4. Verify
- Last completed action: 两张误发私聊样卡已精确撤回，群内正式通知保留；默认路由已改为直接发国内电商群。
- Next concrete action: 提交、发布 v0.3.4，并核对 Zeabur 版本与健康状态。

## Files/Resources

| Path or URL | Purpose |
|---|---|
| `app/cost_gap_alert.py` | 缺口责任分流与发送 |
| `app/cards.py` | 运营缺口卡文案 |
| `app/task_runner.py` | 自动触发类别 |
| `tests/test_cost_gap_routing.py` | 分流与收件路由测试 |
| `docs/cost_gap_responsibility_routing_2026-08-11.md` | 项目修复记录 |
