# Decision Log: 国内电商毛利缺口统一归运营

| Time | Decision | Rationale | Confirmed by | Impact |
|---|---|---|---|---|
| 2026-08-12 | 订单、采购成本、物流成本、平台资料缺口统一由国内电商运营补充或协调 | 避免采购与运营来回判断；一个岗位对报表资料完整性闭环 | user | 自动缺口卡只发国内电商群并 @ 当前岗位人员 |
| 2026-08-12 | 当前漏发项用一张汇总信息卡补发 | 105 行逐条发卡会造成消息轰炸；汇总仍给出数量、原因与动作 | user + report evidence | 实发 1 张无回调信息卡 |

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

