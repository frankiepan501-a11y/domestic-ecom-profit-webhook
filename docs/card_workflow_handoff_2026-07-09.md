# 国内电商毛利报表卡片化工作流交接

日期：2026-07-09

## 背景

旧流程依赖运营和财务进入 Base 任务台手动改状态。问题不是单次操作慢，而是每月反复出现“催资料、判缺口、等确认、追口径”的交接断点。新流程把 Base 降级为系统 ledger，业务人员只通过飞书卡片和上传页完成动作。

## 本次改动

- 新增 `app/ledger.py`：封装报表运行台、资料清单附件台、缺口例外台、输出报表台、审计日志台的读写。
- 新增 `app/cards.py`：运营资料提交卡、P0 缺口卡、财务确认卡、已处理结果卡。
- 新增 `app/card_workflow.py`：月度 run 创建、资料清单生成、P0 gate、试算触发、财务确认、callback 幂等与 PATCH。
- 上传页写新附件台后，会按 `run_id/platform/shop/file_type` 自动镜像附件到旧任务台对应附件字段，保证现有 `task_runner` 无需重写即可读到卡片上传资料。
- P0 上传页已升级为逐行资料工作台：按 `平台/店铺/文件类型` 展示状态和已有附件，支持单项追加上传、文件夹批量上传自动归类、店铺级“无广告消耗”确认、店铺级“无结算”确认、物流暂缺说明和提交初检。单项追加上传会保留旧附件并去重，不要求运营从 0 重传。
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

## 上传工作台 P0 行为

运营收到资料提交卡后进入 `/upload?run_id=...&token=...`。飞书卡片不再放全局“本月无广告消耗/本月无结算”按钮，避免把店铺级判断误写成整月判断。

- 单项上传：每一行可上传多个文件。新上传会和已有 `附件/file_token_json` 合并并按文件名去重，然后继续镜像到旧任务台附件字段。
- 文件夹上传：浏览器选择本地文件夹，前端把 `webkitRelativePath` 作为文件名提交。服务端按文件路径中的平台、店铺、文件类型关键词匹配 manifest。未识别或匹配不唯一的文件只回显给运营，不静默写入。
- 文件夹归类规则已补店铺别名和结算文件识别：`宝空/宝宝/POWKONG` 可匹配 `POWKONG旗舰店/宝空店`，`纷岚/FUNLAB/梵乐璞` 可匹配 `纷岚店`；`交易货款/结算订单/商品结算明细/订单结算明细对账/货款明细/到账` 归入独立必交项 `当月结算账单`，不得并入 `平台费用`；`返点积分/光合平台/软件服务费/淘金币/消费券/消费者体验提升计划服务费` 等天猫账单明细归入 `平台费用`。附件展示和合并按文件 basename 去重，避免文件夹前缀导致重复。
- 2026-07-09 上传卡顿修复：Zeabur 服务启动改为 2 个 uvicorn worker；同一资料项内多个附件上传飞书 Drive 时使用 3 并发，减少天猫平台费用这类多 CSV 场景的等待；前端在文件夹提交后禁用按钮并显示“正在上传/写 ledger”，避免运营误判为空白卡死。该修复不清空旧附件，仍按 basename 合并去重。
- 无广告确认：只出现在 `广告账单` 行，点击后仅把该店铺该月广告账单 manifest 标为 `已确认无数据`，并关闭对应 `广告证据缺失` P0 gap。
- 无结算确认：只出现在 `订单明细/退款明细/当月结算账单` 行，点击后按同一 `平台+店铺` 同步把这些未上传资料项标为 `已确认无数据`。不会把整月 run 改成 `本期无结算已确认`，也不会把结算主文件伪装成平台费用。
- 本期暂缓平台：2026-06 P0 增加 `defer_platform_scope` 上传页动作，当前仅允许淘宝/拼多多。用于“平台毛利口径未统一，本期暂缓纳入四平台试算，后续补充”的范围裁决；不会伪装成“无结算”，而是把该平台资料项标为 `已关闭`，写入 P1 `平台口径暂缓` 例外并关闭该平台 P0 缺口。
- 初检缺口自愈：提交初检时会先按已闭环的资料项自动关闭历史 P0 缺口，避免“先生成缺口、后上传附件”后仍被旧缺口阻断试算。
- 物流暂缺说明：只记录 `物流账单缺失` gap 和 manifest `待补充`，不会放行 P0 gate。
- 提交初检：上传页顶部 `提交初检` 调 `initial_gate_and_maybe_run`。有 P0 缺口则按 Frankie-only 测试链路发缺口卡；无缺口则进入试算。

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
- 2026-06 结算文件驱动链路已本地验证并输出测试报表：`https://u1wpma3xuhr.feishu.cn/sheets/ANKesGax1hnG2BtUgCaczfnFnLh`。测试时设置 `REPORT_SUPPRESS_NOTIFY=1`，未通知运营/财务。
- 2026-07-09 晚从本地基准资料目录 `D:\Users\Administrator\Desktop\财务毛利报表计算资料\深圳奥迪尔\2026年6月` 追加补齐旧任务台和 ledger 附件：京东纷岚 `订单结算明细对账.csv`、京东宝空 `本月无结算明细.txt`、小红书纷岚 `本月无结算订单.txt`，以及对应无订单/无广告/退款证据。原始空 txt 上传时写入了最小说明内容，保留原文件名作为运营无数据确认。
- A/B 对账产物：`domestic_2026_06_ab_compare.xlsx`、`domestic_2026_06_ab_compare.md`。结果为 `PASS`：天猫广告 9391.83、天猫纷岚负毛利、抖音顺丰 API fallback、小红书退款成本扣减、税务A_B核对 sheet 均 PASS；产品毛利、SKU成本、物流匹配、费用明细、缺口清单、输出 sheet 规范均已对齐。
- 京东纷岚 `短信服务费 0.18` 已由 `2026-07-09_18403730_ctzQRJ8BQj9JF8zkCs5V_订单结算明细对账.csv` 读取到 3 笔 `0.06` 支出，并进入 `费用明细汇总` 和 `月度毛利试算`。
- 修复 2026-07-09 重跑时成本缺口告警刷屏：`REPORT_SUPPRESS_NOTIFY=1` 现在同时抑制成本告警；告警只在最终结算签核口径产生 `采购成本` P0 时发送，并写审计幂等键，重复重跑不会重复弹。
- 新增 `/cards/finance-confirm`：可从旧任务台最新报表链接补建输出报表台并发送真实财务确认卡，默认 `frankie_only=true`。2026-06 真实卡已发 Frankie 测试，message_id=`om_x100b6bcff9b40ca0c2377623b97ce94`。
- 财务确认卡初版曾改为结论优先，但仍带 `P0/Gate` 等系统词；该版本已被 2026-07-10 二次修复替代，当前财务可见文案以“资料/成本缺失、金额异常、临时估算说明”表达。
- 修复 2026-07-10 财务确认卡输出包权限缺口：现有 2026-06 报表已补授权给 Frankie、吴晓丹和财务/采购/物流仓储/电商运营部门成员；后续 `send_finance_card` 发卡前会对 sheets 输出包再次执行 `_grant_report_collaborators`，避免卡片链接可见但负责人无权打开。
- 2026-07-10 公司规则更新：月度毛利报表确认卡只核对平台当月毛利报表，统计口径固定为结算月；国内平台月度毛利确认卡按平台拆分发送（2026-06 为抖音、天猫、小红书、京东四张卡）；月度毛利卡不做平台涉税金额比对，涉税金额比对另建季度初卡片工作流核对上一季度。当前 `/cards/finance-confirm` 已按该规则返回/发送平台级卡片，按钮 payload 带 `platform`，四个平台全部确认/接受临时估算后才归档 run；云盘保存位置、输出包链接和导航入口沿用当前报表输出链路，不在月度卡片改造中迁移目录。
- 2026-07-10 财务确认卡样式二次修复：财务可见正文禁止使用 `P0/P1/Gate` 等系统内部词，改为“资料/成本缺失检查、财务需关注的金额异常、临时估算或本期暂缓说明”。平台卡标题必须直接写平台名，卡内必须列本平台店铺、净销售额、毛利、毛利率、广告费、采购成本、物流费用和本平台报表入口；负毛利只作为财务关注项展示，不自动等同资料缺口。按钮文案使用财务语言：确认该平台定稿、退回资料缺失、退回金额/口径异常、接受本期临时估算。点击后的已处理卡也不能暴露 raw action，只显示业务结果、报表批次和处理编号。
- 2026-07-10 财务确认卡按钮逻辑修复：底部按钮不是多选题，而是一次性财务决定，点击后原卡会 PATCH 成已处理态。为覆盖业务组合场景，确认类按钮动态二选一：无例外时显示“确认该平台定稿”；存在临时估算/本期暂缓说明时显示“确认定稿（接受上述例外）”，该按钮本身就等于确认定稿，不需要再点其他确认按钮。退回类按钮保留“退回资料缺失”“退回金额/口径异常”，并新增“同时退回资料和金额问题”，用于资料不完整且金额/口径也需要解释或重算的情况。
- 2026-07-10 财务退回后续闭环：财务点“退回资料缺失 / 退回金额或口径异常 / 同时退回”后，callback 不再只写 ledger 状态，会自动发送后续处理卡。资料退回卡指向上传页并要求补结算单、广告证明、物流账单、成本资料或无数据证明；口径退回卡要求修正销售额、退款、平台费、广告费、采购成本、物流费、其他费用归类或补充业务原因；组合退回同时要求补资料和修正口径。后续卡按钮会 PATCH 原卡并触发重新初检/重新试算，完成后重新发送平台财务确认卡。测试期仍受 `CARD_WORKFLOW_FRANKIE_ONLY=true` 约束，只发 Frankie。

## 剩余风险和下一步

P0 卡片上传与结算文件驱动试算已可用。当前 2026-06 A/B 已通过，可以进入财务确认卡测试/确认链路；淘宝、拼多多仍按用户决策暂缓，后续口径统一后单独补做。

建议下一步：

- P0：Frankie 检查按平台拆分后的财务确认卡样式和按钮。确认无误后调用 `/cards/finance-confirm?year_month=2026-06&frankie_only=false` 发给财务。
- P0：财务确认后自动写输出报表台、归档状态和汇总索引；保留重复点击幂等验证。
- P0：补建国内平台季度涉税金额核对卡片工作流，季度初核对上一季度，尤其适配天猫这类按季度出涉税金额数据的平台。
- P0：把本次“本地补件但卡片上传页未触发”的场景沉淀为运维动作：允许 Frankie/系统管理员从本地资料目录追加回填，不要求运营重新从 0 上传。
- P1：把上传页附件按平台 parser 预检，失败写缺口例外台并发 P0 缺口卡。
- P1：将 manifest 创建从逐条 create 优化为 batch_create，降低首次新月份发卡耗时。
- P1：把财务确认后的汇总索引/归档写入做成独立可回放端点。
- P2：将 `/cards/test-samples` 保留为 smoke，但在生产月结后隐藏或限制为 Frankie only。
