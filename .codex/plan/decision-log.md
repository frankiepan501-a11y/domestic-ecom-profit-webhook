# Decision Log: 国内电商毛利缺口统一归运营

| Time | Decision | Rationale | Confirmed by | Impact |
|---|---|---|---|---|
| 2026-08-12 | 订单、采购成本、物流成本、平台资料缺口统一由国内电商运营补充或协调 | 避免采购与运营来回判断；一个岗位对报表资料完整性闭环 | user | 自动缺口卡只发国内电商群并 @ 当前岗位人员 |
| 2026-08-12 | 当前漏发项用一张汇总信息卡补发 | 105 行逐条发卡会造成消息轰炸；汇总仍给出数量、原因与动作 | user + report evidence | 实发 1 张无回调信息卡 |
| 2026-08-12 | 无审批/无回调的缺口信息卡不再反复发 Frankie 样卡 | 业务收件人已明确是国内运营，格式可由结构自检和回读验证 | user correction | 默认直接发运营群；两张误发私聊样卡已撤回 |

## Rejected Options

| Option | Rejected because | Evidence |
|---|---|---|
| ERP SKU 已识别后直接派采购 | 与用户最新责任口径冲突 | 用户本轮明确纠正 |
| 仅保留订单与采购成本触发 | 会继续漏掉物流成本和资料缺口 | 2026-07 报表现有 107 行相关漏发项 |

## Routing/Ownership Notes

- Business owner: 国内电商运营（当前按职务解析为赵伟俊）。
- App/bot/credential namespace: 聪哥分身3号发送群卡及 @。
- Source of truth: 2026-07 新报表“缺口清单” + 用户最新责任口径。
- Write permissions: 用户已明确授权群内补发；代码与部署按当前修复执行。

## 2026-08-12 P0 整改追加决策

| Decision | Rationale | Confirmed by | Impact |
|---|---|---|---|
| 国内毛利只能读取运营上传附件，不登录抖店或其他平台后台取数 | 用户明确指出原流程不需要 AI 登录平台 | user correction | 浏览器分支停止；README/交接/候选记忆固定边界 |
| 静默重跑只 PATCH 原卡；找不到原卡或 PATCH 不全时失败关闭，不补发 | 防止重复卡片和通知风暴 | user + screenshot P0 | `missing/failed` 任一非零即返回失败 |
| 宝空结算资料按 6 个表头字段判定，不能用“结算单”文件名猜 | 现有文件实际是涉税报送，无法订单级核算 | uploaded attachment evidence | 只留一条精确补件说明 |
| 财务回调以公司运行台“已灌总表 + 已归档”为最终证明 | HTTP 200 不等于总表已写入 | workflow contract | 未达双终态不得归档国内报表 |
