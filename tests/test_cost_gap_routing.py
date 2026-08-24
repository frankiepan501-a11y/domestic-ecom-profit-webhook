import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import cards, cost_gap_alert, feishu


class CostGapClassificationTests(unittest.TestCase):
    def test_existing_gap_cards_sort_iso_and_numeric_created_times(self):
        audits = [
            {"fields": {
                "action": "cost_gap_alert_v2",
                "target_id": "operations",
                "result": "sent",
                "created_at": "2026-07-01T10:00:00+08:00",
                "after_json": '{"message_ids":["om_iso"],"channel":"group"}',
            }},
            {"fields": {
                "action": "cost_gap_alert_v2",
                "target_id": "operations",
                "result": "sent",
                "created_at": 1782957600000,
                "after_json": '{"message_ids":["om_numeric"],"channel":"group"}',
            }},
        ]

        self.assertEqual(
            ["om_iso", "om_numeric"],
            cost_gap_alert._existing_group_message_ids(audits),
        )

    def test_existing_gap_cards_accept_feishu_rich_text_field_shape(self):
        audits = [{"fields": {
            "action": [{"text": "cost_gap_alert_v2"}],
            "target_id": [{"text": "operations"}],
            "result": [{"text": "sent"}],
            "created_at": 1786517994700,
            "after_json": [{"text": '{"message_ids":["om_current"],"channel":"group"}'}],
        }}]

        self.assertEqual(
            ["om_current"],
            cost_gap_alert._existing_group_message_ids(audits),
        )

    def test_order_without_merchant_code_routes_to_operations(self):
        settlement = {
            "gap_rows": [[
                "P0", "天猫", "天猫宝空", "2026-07", "采购成本",
                "5117032694368093324", "订单无法取得商家编码/外部系统编号",
                "无法映射采购成本", "补订单明细商家编码或SKU对照表",
            ]],
            "cost_rows": [[
                "天猫", "天猫宝空", "2026-07", "5117032694368093324", "",
                "POWKONG PLANTDOCK 食人花底座", 1, 0, 0, "成本缺失/为0",
            ]],
        }

        classified = cost_gap_alert.classify_settlement_cost_gaps(settlement)

        self.assertEqual([], classified["procurement"])
        self.assertEqual("订单号", classified["operations"][0]["object_type"])
        self.assertEqual("5117032694368093324", classified["operations"][0]["order_id"])
        self.assertEqual("POWKONG PLANTDOCK 食人花底座", classified["operations"][0]["name"])

    def test_known_erp_sku_with_zero_cost_routes_to_operations(self):
        settlement = {
            "gap_rows": [[
                "P0", "抖音", "抖音纷岚", "2026-07", "采购成本",
                "FL-DOCK-001", "采购成本表未匹配或成本为0",
                "毛利会虚高", "维护产品采购成本台后重跑",
            ]],
            "cost_rows": [[
                "抖音", "抖音纷岚", "2026-07", "order-001", "FL-DOCK-001",
                "FUNLAB Dock", 2, 0, 0, "成本缺失/为0",
            ]],
        }

        classified = cost_gap_alert.classify_settlement_cost_gaps(settlement)

        self.assertEqual([], classified["procurement"])
        self.assertEqual("ERP SKU", classified["operations"][0]["object_type"])
        self.assertEqual("FL-DOCK-001", classified["operations"][0]["erp_sku"])
        self.assertEqual("FUNLAB Dock", classified["operations"][0]["name"])

    def test_logistics_and_source_file_gaps_route_to_operations(self):
        settlement = {
            "gap_rows": [
                [
                    "P0", "天猫", "天猫宝空", "2026-07", "物流成本",
                    "SF123", "结算订单运单未在前后月账单池命中，顺丰API也未返回费用",
                    "毛利会虚高", "补后续账单或核实运单/API权限后重跑",
                ],
                [
                    "P0", "抖音", "抖音纷岚", "2026-07", "资料缺口",
                    "抖音纷岚", "缺结算订单或订单明细文件",
                    "无法计算结算口径毛利", "补结算订单和订单明细后重跑",
                ],
                [
                    "P0", "抖音", "抖音宝空", "2026-07", "物流成本",
                    "order-without-waybill", "结算收入订单未解析到有效运单号",
                    "无法计算订单物流成本", "补订单明细物流单号或物流账单",
                ],
            ],
            "cost_rows": [],
        }

        classified = cost_gap_alert.classify_settlement_cost_gaps(settlement)

        self.assertEqual([], classified["procurement"])
        self.assertEqual([], classified["finance_review"])
        self.assertEqual(["运单号", "店铺", "订单号"], [x["object_type"] for x in classified["operations"]])
        self.assertEqual(["物流成本", "资料缺口", "物流成本"], [x["gap_category"] for x in classified["operations"]])

    def test_ad_evidence_gap_preserves_file_type_object(self):
        settlement = {
            "gap_rows": [[
                "P0", "抖音", "抖音宝空", "2026-06", "资料缺口",
                "广告账单", "已检查当前附件：抖音宝空订单明细.csv；未提交广告账单",
                "广告费可能漏计，毛利会虚高", "上传广告账单，或确认本月无广告消耗",
            ]],
            "cost_rows": [],
        }

        classified = cost_gap_alert.classify_settlement_cost_gaps(settlement)

        self.assertEqual(1, len(classified["operations"]))
        self.assertEqual("资料类型", classified["operations"][0]["object_type"])
        self.assertEqual("广告账单", classified["operations"][0]["object"])

    def test_same_erp_sku_across_shops_keeps_matching_shop_context(self):
        settlement = {
            "gap_rows": [[
                "P0", "抖音", "抖音纷岚", "2026-07", "采购成本",
                "SHARED-SKU", "采购成本表未匹配或成本为0",
                "毛利会虚高", "维护产品采购成本台后重跑",
            ]],
            "cost_rows": [
                [
                    "天猫", "天猫宝空", "2026-07", "tm-order-001", "SHARED-SKU",
                    "天猫商品", 1, 0, 0, "成本缺失/为0",
                ],
                [
                    "抖音", "抖音纷岚", "2026-07", "dy-order-001", "SHARED-SKU",
                    "抖音商品", 1, 0, 0, "成本缺失/为0",
                ],
            ],
        }

        classified = cost_gap_alert.classify_settlement_cost_gaps(settlement)

        item = classified["operations"][0]
        self.assertEqual("dy-order-001", item["order_id"])
        self.assertEqual("抖音商品", item["name"])

    def test_order_detail_match_gap_routes_to_operations(self):
        settlement = {
            "gap_rows": [[
                "P0", "抖音", "抖音纷岚", "2026-07", "订单明细匹配",
                "order-404", "结算订单未在订单明细中找到",
                "无法补商家编码和物流单号", "补覆盖该订单下单时间范围的订单明细后重跑",
            ]],
            "cost_rows": [],
        }

        classified = cost_gap_alert.classify_settlement_cost_gaps(settlement)

        self.assertEqual("order-404", classified["operations"][0]["order_id"])
        self.assertEqual([], classified["procurement"])

    def test_zero_cost_wording_without_erp_sku_evidence_still_routes_to_operations(self):
        settlement = {
            "gap_rows": [[
                "P0", "抖音", "抖音纷岚", "2026-07", "采购成本",
                "order-looks-like-id", "采购成本表未匹配或成本为0",
                "毛利会虚高", "维护产品采购成本台后重跑",
            ]],
            "cost_rows": [],
        }

        classified = cost_gap_alert.classify_settlement_cost_gaps(settlement)

        self.assertEqual([], classified["procurement"])
        self.assertEqual([], classified["finance_review"])
        self.assertEqual("order-looks-like-id", classified["operations"][0]["object"])
        self.assertEqual("待核实对象", classified["operations"][0]["object_type"])

    def test_zero_cost_wording_with_positive_unit_cost_still_routes_to_operations(self):
        settlement = {
            "gap_rows": [[
                "P0", "抖音", "抖音纷岚", "2026-07", "采购成本",
                "FL-DOCK-001", "采购成本表未匹配或成本为0",
                "毛利会虚高", "维护产品采购成本台后重跑",
            ]],
            "cost_rows": [[
                "抖音", "抖音纷岚", "2026-07", "order-001", "FL-DOCK-001",
                "FUNLAB Dock", 2, 15.5, 31.0, "产品采购成本台",
            ]],
        }

        classified = cost_gap_alert.classify_settlement_cost_gaps(settlement)

        self.assertEqual([], classified["procurement"])
        self.assertEqual([], classified["finance_review"])
        self.assertEqual("FL-DOCK-001", classified["operations"][0]["object"])

    def test_tmall_settlement_row_enriches_order_dates_for_operations(self):
        csv_text = (
            "订单号,下单时间,确认收货时间,商品ID,sku,商品名称\n"
            "5117032694368093324,2026-05-23 16:14:12,2026-07-08 19:46:20,"
            "1020063375536,6197249830344|颜色分类#3B食人花2代,POWKONG食人花底座\n"
        )
        raw = {
            "source_files": [{
                "platform": "天猫",
                "shop": "POWKONG旗舰店",
                "fname": "交易货款_202607_202607.csv",
                "buf": csv_text.encode("utf-8"),
            }]
        }
        settlement = {
            "gap_rows": [[
                "P0", "天猫", "天猫宝空", "2026-07", "采购成本",
                "5117032694368093324", "订单无法取得商家编码/外部系统编号",
                "无法映射采购成本", "补订单明细商家编码或SKU对照表",
            ]],
            "cost_rows": [],
        }

        context = cost_gap_alert.extract_order_context(raw)
        classified = cost_gap_alert.classify_settlement_cost_gaps(settlement, order_context=context)
        item = classified["operations"][0]

        self.assertEqual("2026-05-23 16:14:12", item["order_time"])
        self.assertEqual("2026-07-08 19:46:20", item["settled_time"])
        self.assertEqual("1020063375536", item["platform_product_id"])
        self.assertEqual("6197249830344|颜色分类#3B食人花2代", item["platform_sku"])
        self.assertEqual("POWKONG食人花底座", item["name"])

    def test_douyin_settlement_row_enriches_order_date_when_export_provides_it(self):
        csv_text = (
            "订单号,订单创建时间,结算时间,商品ID,商品名称\n"
            "dy-order-001,2026-04-18 09:30:00,2026-07-12 20:00:00,p-001,抖音商品A\n"
        )
        raw = {
            "source_files": [{
                "platform": "抖音",
                "shop": "抖音纷岚",
                "fname": "结算订单_202607.csv",
                "buf": csv_text.encode("utf-8"),
            }]
        }

        context = cost_gap_alert.extract_order_context(raw)

        self.assertEqual("2026-04-18 09:30:00", context["dy-order-001"]["order_time"])
        self.assertEqual("2026-07-12 20:00:00", context["dy-order-001"]["settled_time"])


class CostGapCardTests(unittest.TestCase):
    def test_operations_card_contains_decision_context_and_exact_export_range(self):
        card = cards.cost_gap_alert_card(
            "2026-07",
            "operations",
            [{
                "platform": "天猫",
                "shop": "天猫宝空",
                "object_type": "订单号",
                "object": "5117032694368093324",
                "order_id": "5117032694368093324",
                "erp_sku": "",
                "name": "POWKONG PLANTDOCK 食人花底座",
                "problem": "订单无法取得商家编码/外部系统编号",
                "impact": "无法映射采购成本",
                "action": "补订单明细商家编码或SKU对照表",
                "order_time": "2026-05-23 16:14:12",
                "settled_time": "2026-07-08 19:46:20",
            }],
            mention_open_ids=["ou_event_zhao"],
            action_url="https://domestic-ecom-profit.zeabur.app/upload?run_id=run-2026-07",
        )

        rendered = str(card)
        self.assertIn("国内电商毛利资料/成本缺口", rendered)
        self.assertIn("国内电商运营统一跟进，采购不直接收卡", rendered)
        self.assertIn("天猫宝空", rendered)
        self.assertIn("订单号", rendered)
        self.assertIn("5117032694368093324", rendered)
        self.assertIn("POWKONG PLANTDOCK 食人花底座", rendered)
        self.assertIn("无法映射采购成本", rendered)
        self.assertIn("下单：2026-05-23 16:14:12", rendered)
        self.assertIn("结算/收货：2026-07-08 19:46:20", rendered)
        self.assertIn("2026-05-01—2026-07-31", rendered)
        self.assertIn("<at id=ou_event_zhao></at>", rendered)
        self.assertIn("打开资料上传页", rendered)

    def test_operations_card_uses_all_entries_for_range_and_count(self):
        entries = [{
            "platform": "天猫",
            "shop": "天猫宝空",
            "object": f"order-{i:03d}",
            "order_id": f"order-{i:03d}",
            "order_time": "2026-06-02 10:00:00",
            "problem": "商家编码为空",
            "impact": "无法映射采购成本",
        } for i in range(25)]
        entries.append({
            "platform": "天猫",
            "shop": "天猫宝空",
            "object": "order-026",
            "order_id": "order-026",
            "order_time": "2026-05-03 10:00:00",
            "problem": "商家编码为空",
            "impact": "无法映射采购成本",
        })

        card = cards.cost_gap_alert_card(
            "2026-07",
            "operations",
            entries[:25],
            all_entries=entries,
            page_index=1,
            page_count=2,
        )

        rendered = str(card)
        self.assertIn("2026-05-01—2026-07-31", rendered)
        self.assertIn("共 26 条", rendered)
        self.assertIn("第 1/2 张", rendered)

    def test_operations_card_keeps_platform_and_shop_on_each_line(self):
        card = cards.cost_gap_alert_card(
            "2026-07",
            "operations",
            [
                {
                    "platform": "天猫", "shop": "天猫宝空", "order_id": "tm-001",
                    "name": "商品A", "problem": "商家编码为空", "impact": "无法算成本",
                    "order_time": "2026-05-01 10:00:00",
                },
                {
                    "platform": "抖音", "shop": "抖音纷岚", "order_id": "dy-001",
                    "name": "商品B", "problem": "订单明细未匹配", "impact": "无法算成本",
                    "order_time": "2026-06-01 10:00:00",
                },
            ],
        )

        rendered = str(card)
        self.assertIn("天猫/天猫宝空｜资料/成本｜订单号 tm-001", rendered)
        self.assertIn("抖音/抖音纷岚｜资料/成本｜订单号 dy-001", rendered)

    def test_missing_order_date_uses_conservative_exact_export_range(self):
        card = cards.cost_gap_alert_card(
            "2026-07",
            "operations",
            [{
                "platform": "抖音", "shop": "抖音纷岚", "order_id": "dy-404",
                "name": "商品未取得", "problem": "订单明细未匹配", "impact": "无法补商家编码",
                "order_time": "",
            }],
        )

        rendered = str(card)
        self.assertIn("2026-04-01—2026-07-31", rendered)
        self.assertNotIn("覆盖缺口订单下单日期", rendered)


class CostGapNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_silent_refresh_patches_latest_gap_card_and_invalidates_older_duplicates(self):
        settlement = {
            "gap_rows": [[
                "P0", "抖音", "抖音宝空", "2026-07", "资料缺口",
                "抖音宝空", "已上传文件不是可按订单核算的结算明细",
                "无法逐订单核算结算收入及平台扣费",
                "请补导含订单号、结算金额、收入合计、商品数量和商品ID的结算订单明细",
            ]],
            "cost_rows": [],
        }
        audits = [
            {
                "fields": {
                    "action": "cost_gap_alert_v2",
                    "target_id": "operations",
                    "result": "sent",
                    "created_at": 100,
                    "after_json": '{"message_ids":["om_old"],"channel":"group"}',
                }
            },
            {
                "fields": {
                    "action": "cost_gap_alert_v2",
                    "target_id": "operations",
                    "result": "sent",
                    "created_at": 200,
                    "after_json": '{"message_ids":["om_current"],"channel":"group"}',
                }
            },
        ]
        with patch.object(
            cost_gap_alert.ledger,
            "find_many",
            new=AsyncMock(return_value=audits),
        ), patch(
            "app.card_workflow._ops_group_mentions",
            new=AsyncMock(return_value={"ou_event_zhao": "赵伟俊"}),
        ), patch.object(
            feishu,
            "patch_message_card",
            new=AsyncMock(return_value={"code": 0}),
        ) as patch_card, patch.object(
            cost_gap_alert.ledger,
            "finalize_audit",
            new=AsyncMock(),
        ), patch(
            "app.card_workflow._send_ops_card",
            new=AsyncMock(side_effect=AssertionError("静默重跑不得新增卡片")),
        ):
            result = await cost_gap_alert.refresh_existing_operation_gap_cards(
                "2026-07", settlement, {}
            )

        self.assertEqual(["om_current"], result["active_message_ids"])
        self.assertEqual(["om_old"], result["invalidated_message_ids"])
        self.assertEqual(0, result["missing_existing_cards"])
        self.assertEqual(2, patch_card.await_count)
        rendered_by_message = {
            call.args[0]: str(call.args[1]) for call in patch_card.await_args_list
        }
        self.assertIn("抖音宝空", rendered_by_message["om_current"])
        self.assertIn("结算金额", rendered_by_message["om_current"])
        self.assertIn("<at id=ou_event_zhao></at>", rendered_by_message["om_current"])
        self.assertIn("本卡无效", rendered_by_message["om_old"])

    async def test_zero_cost_logistics_and_source_gaps_send_only_to_operations_group(self):
        settlement = {
            "gap_rows": [
                [
                    "P0", "抖音", "抖音纷岚", "2026-07", "采购成本",
                    "FL-DOCK-001", "采购成本表未匹配或成本为0",
                    "毛利会虚高", "维护产品采购成本台后重跑",
                ],
                [
                    "P0", "天猫", "天猫宝空", "2026-07", "物流成本",
                    "SF123", "结算订单运单未在前后月账单池命中，顺丰API也未返回费用",
                    "毛利会虚高", "补后续账单或核实运单/API权限后重跑",
                ],
                [
                    "P0", "抖音", "抖音宝空", "2026-07", "资料缺口",
                    "抖音宝空", "缺结算订单或订单明细文件",
                    "无法计算结算口径毛利", "补结算订单和订单明细后重跑",
                ],
            ],
            "cost_rows": [[
                "抖音", "抖音纷岚", "2026-07", "order-001", "FL-DOCK-001",
                "FUNLAB Dock", 2, 0, 0, "成本缺失/为0",
            ]],
        }
        with patch(
            "app.card_workflow._ops_group_mentions",
            new=AsyncMock(return_value={"ou_event_zhao": "赵伟俊"}),
        ), patch(
            "app.card_workflow._send_ops_card",
            new=AsyncMock(return_value=(["om_ops"], ["国内电商平台沟通群"], "group")),
        ) as send_ops, patch.object(
            feishu,
            "resolve_users_by_job_title",
            new=AsyncMock(side_effect=AssertionError("国内毛利缺口不应再解析采购岗位")),
        ), patch.object(
            feishu,
            "send_interactive_open_id",
            new=AsyncMock(side_effect=AssertionError("国内毛利缺口不应再私聊采购或财务")),
        ), patch.object(
            cost_gap_alert.ledger,
            "audit_exists",
            new=AsyncMock(return_value=False),
        ), patch.object(
            cost_gap_alert.ledger,
            "write_audit",
            new=AsyncMock(),
        ):
            result = await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=False
            )

        self.assertEqual(["om_ops"], result["operations"])
        self.assertEqual([], result["procurement"])
        self.assertEqual([], result["finance_review"])
        rendered = str(send_ops.await_args.args[0])
        self.assertIn("采购成本", rendered)
        self.assertIn("物流成本", rendered)
        self.assertIn("资料缺口", rendered)
        self.assertIn("<at id=ou_event_zhao></at>", rendered)

    async def test_frankie_only_operations_gap_does_not_resolve_or_mention_operations(self):
        settlement = {
            "gap_rows": [[
                "P0", "天猫", "天猫宝空", "2026-07", "采购成本",
                "5117032694368093324", "订单无法取得商家编码/外部系统编号",
                "无法映射采购成本", "补订单明细商家编码或SKU对照表",
            ]],
            "cost_rows": [],
        }
        with patch(
            "app.card_workflow._ops_group_mentions",
            new=AsyncMock(side_effect=AssertionError("Frankie-only must not resolve operations")),
        ), patch(
            "app.card_workflow._send_ops_card",
            new=AsyncMock(return_value=(["om_frankie"], ["潘志聪"], "private")),
        ) as send_ops, patch.object(
            cost_gap_alert.ledger,
            "audit_exists",
            new=AsyncMock(return_value=False),
        ), patch.object(
            cost_gap_alert.ledger,
            "write_audit",
            new=AsyncMock(),
        ):
            result = await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=True
            )

        self.assertEqual(["om_frankie"], result["operations"])
        self.assertEqual(True, send_ops.await_args.kwargs["frankie_only"])
        self.assertNotIn("<at id=", str(send_ops.await_args.args[0]))

    async def test_frankie_only_zero_cost_gap_uses_operations_preview_route(self):
        settlement = {
            "gap_rows": [[
                "P0", "抖音", "抖音纷岚", "2026-07", "采购成本",
                "FL-DOCK-001", "采购成本表未匹配或成本为0",
                "毛利会虚高", "维护产品采购成本台后重跑",
            ]],
            "cost_rows": [[
                "抖音", "抖音纷岚", "2026-07", "order-001", "FL-DOCK-001",
                "FUNLAB Dock", 2, 0, 0, "成本缺失/为0",
            ]],
        }
        with patch(
            "app.card_workflow._ops_group_mentions",
            new=AsyncMock(side_effect=AssertionError("Frankie-only must not resolve operations")),
        ), patch(
            "app.card_workflow._send_ops_card",
            new=AsyncMock(return_value=(["om_frankie"], ["潘志聪"], "private")),
        ) as send_ops, patch.object(
            cost_gap_alert.ledger,
            "audit_exists",
            new=AsyncMock(return_value=False),
        ), patch.object(
            cost_gap_alert.ledger,
            "write_audit",
            new=AsyncMock(),
        ):
            result = await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=True
            )

        self.assertEqual(["om_frankie"], result["operations"])
        self.assertEqual([], result["procurement"])
        self.assertEqual(True, send_ops.await_args.kwargs["frankie_only"])

    async def test_operations_gaps_are_sent_in_pages_of_25(self):
        gap_rows = []
        for i in range(26):
            gap_rows.append([
                "P0", "天猫", "天猫宝空", "2026-07", "采购成本",
                f"order-{i:03d}", "订单无法取得商家编码/外部系统编号",
                "无法映射采购成本", "补订单明细商家编码或SKU对照表",
            ])
        settlement = {"gap_rows": gap_rows, "cost_rows": []}
        with patch(
            "app.card_workflow._ops_group_mentions",
            new=AsyncMock(return_value={"ou_event_zhao": "赵伟俊"}),
        ), patch(
            "app.card_workflow._send_ops_card",
            new=AsyncMock(side_effect=[
                (["om_page_1"], ["国内电商平台沟通群"], "group"),
                (["om_page_2"], ["国内电商平台沟通群"], "group"),
            ]),
        ) as send_ops, patch.object(
            cost_gap_alert.ledger,
            "audit_exists",
            new=AsyncMock(return_value=False),
        ), patch.object(
            cost_gap_alert.ledger,
            "write_audit",
            new=AsyncMock(),
        ):
            result = await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=False
            )

        self.assertEqual(["om_page_1", "om_page_2"], result["operations"])
        self.assertEqual(2, send_ops.await_count)
        self.assertIn("第 2/2 张", str(send_ops.await_args_list[1].args[0]))

    async def test_frankie_preview_does_not_block_later_production_send(self):
        settlement = {
            "gap_rows": [[
                "P0", "天猫", "天猫宝空", "2026-07", "采购成本",
                "order-001", "订单无法取得商家编码/外部系统编号",
                "无法映射采购成本", "补订单明细商家编码或SKU对照表",
            ]],
            "cost_rows": [],
        }
        stored_keys = set()

        async def audit_exists(key):
            return key in stored_keys

        async def write_audit(key, *args, **kwargs):
            stored_keys.add(key)

        with patch(
            "app.card_workflow._ops_group_mentions",
            new=AsyncMock(return_value={"ou_event_zhao": "赵伟俊"}),
        ), patch(
            "app.card_workflow._send_ops_card",
            new=AsyncMock(side_effect=[
                (["om_preview"], ["潘志聪"], "private"),
                (["om_production"], ["国内电商平台沟通群"], "group"),
            ]),
        ) as send_ops, patch.object(
            cost_gap_alert.ledger,
            "audit_exists",
            new=AsyncMock(side_effect=audit_exists),
        ), patch.object(
            cost_gap_alert.ledger,
            "write_audit",
            new=AsyncMock(side_effect=write_audit),
        ):
            preview = await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=True
            )
            production = await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=False
            )

        self.assertEqual(["om_preview"], preview["operations"])
        self.assertEqual(["om_production"], production["operations"])
        self.assertEqual(2, send_ops.await_count)
        self.assertEqual(2, len(stored_keys))

    async def test_send_without_message_id_is_not_marked_sent_and_can_retry(self):
        settlement = {
            "gap_rows": [[
                "P0", "抖音", "抖音纷岚", "2026-07", "采购成本",
                "FL-DOCK-001", "采购成本表未匹配或成本为0",
                "毛利会虚高", "维护产品采购成本台后重跑",
            ]],
            "cost_rows": [[
                "抖音", "抖音纷岚", "2026-07", "order-001", "FL-DOCK-001",
                "FUNLAB Dock", 2, 0, 0, "成本缺失/为0",
            ]],
        }
        with patch(
            "app.card_workflow._send_ops_card",
            new=AsyncMock(return_value=([], ["潘志聪"], "private")),
        ) as send_ops, patch.object(
            cost_gap_alert.ledger,
            "audit_exists",
            new=AsyncMock(return_value=False),
        ), patch.object(
            cost_gap_alert.ledger,
            "write_audit",
            new=AsyncMock(),
        ) as write_audit:
            await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=True
            )
            await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=True
            )

        self.assertEqual(2, send_ops.await_count)
        write_audit.assert_not_awaited()

    async def test_zero_cost_gap_never_resolves_procurement_fallback(self):
        settlement = {
            "gap_rows": [[
                "P0", "抖音", "抖音纷岚", "2026-07", "采购成本",
                "FL-DOCK-001", "采购成本表未匹配或成本为0",
                "毛利会虚高", "维护产品采购成本台后重跑",
            ]],
            "cost_rows": [[
                "抖音", "抖音纷岚", "2026-07", "order-001", "FL-DOCK-001",
                "FUNLAB Dock", 2, 0, 0, "成本缺失/为0",
            ]],
        }
        with patch(
            "app.card_workflow._ops_group_mentions",
            new=AsyncMock(return_value={"ou_event_zhao": "赵伟俊"}),
        ), patch(
            "app.card_workflow._send_ops_card",
            new=AsyncMock(return_value=(["om_ops"], ["国内电商平台沟通群"], "group")),
        ) as send_ops, patch.object(
            feishu,
            "resolve_users_by_job_title",
            new=AsyncMock(side_effect=AssertionError("不应解析采购岗位")),
        ) as resolve_proc, patch.object(
            cost_gap_alert.ledger,
            "audit_exists",
            new=AsyncMock(return_value=False),
        ), patch.object(
            cost_gap_alert.ledger,
            "write_audit",
            new=AsyncMock(),
        ):
            result = await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=False
            )

        self.assertEqual(["om_ops"], result["operations"])
        self.assertEqual([], result["procurement"])
        self.assertEqual(1, send_ops.await_count)
        resolve_proc.assert_not_awaited()

    async def test_operations_gap_is_idempotent_after_success(self):
        settlement = {
            "gap_rows": [[
                "P0", "抖音", "抖音纷岚", "2026-07", "采购成本",
                "FL-DOCK-001", "采购成本表未匹配或成本为0",
                "毛利会虚高", "维护产品采购成本台后重跑",
            ]],
            "cost_rows": [[
                "抖音", "抖音纷岚", "2026-07", "order-001", "FL-DOCK-001",
                "FUNLAB Dock", 2, 0, 0, "成本缺失/为0",
            ]],
        }
        stored_keys = set()

        async def audit_exists(key):
            return key in stored_keys

        async def write_audit(key, *args, **kwargs):
            stored_keys.add(key)

        with patch(
            "app.card_workflow._ops_group_mentions",
            new=AsyncMock(return_value={"ou_event_zhao": "赵伟俊"}),
        ), patch(
            "app.card_workflow._send_ops_card",
            new=AsyncMock(return_value=(["om_ops"], ["国内电商平台沟通群"], "group")),
        ) as send_ops, patch.object(
            cost_gap_alert.ledger,
            "audit_exists",
            new=AsyncMock(side_effect=audit_exists),
        ), patch.object(
            cost_gap_alert.ledger,
            "write_audit",
            new=AsyncMock(side_effect=write_audit),
        ):
            await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=False
            )
            await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=False
            )

        self.assertEqual(1, send_ops.await_count)
        self.assertEqual(1, len(stored_keys))

    async def test_route_and_recipient_exceptions_do_not_block_other_alerts(self):
        settlement = {
            "gap_rows": [
                [
                    "P0", "天猫", "天猫宝空", "2026-07", "采购成本",
                    "tm-order-001", "订单无法取得商家编码/外部系统编号",
                    "无法映射采购成本", "补订单明细商家编码或SKU对照表",
                ],
                [
                    "P0", "抖音", "抖音纷岚", "2026-07", "采购成本",
                    "FL-DOCK-001", "采购成本表未匹配或成本为0",
                    "毛利会虚高", "维护产品采购成本台后重跑",
                ],
                [
                    "P0", "小红书", "小红书纷岚", "2026-07", "采购成本",
                    "mystery-001", "成本口径冲突，系统无法判断责任",
                    "毛利暂不可确认", "由财务/系统核对",
                ],
            ],
            "cost_rows": [[
                "抖音", "抖音纷岚", "2026-07", "dy-order-001", "FL-DOCK-001",
                "FUNLAB Dock", 2, 0, 0, "成本缺失/为0",
            ]],
        }
        with patch(
            "app.card_workflow._ops_group_mentions",
            new=AsyncMock(return_value={"ou_event_zhao": "赵伟俊"}),
        ), patch(
            "app.card_workflow._send_ops_card",
            new=AsyncMock(side_effect=TimeoutError("运营群发送超时")),
        ), patch.object(
            feishu,
            "send_interactive_open_id",
            new=AsyncMock(side_effect=AssertionError("不应发送采购或财务私聊")),
        ) as send_private, patch.object(
            cost_gap_alert.ledger,
            "audit_exists",
            new=AsyncMock(return_value=False),
        ), patch.object(
            cost_gap_alert.ledger,
            "write_audit",
            new=AsyncMock(),
        ) as write_audit:
            result = await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=False
            )

        self.assertEqual([], result["operations"])
        self.assertEqual([], result["procurement"])
        self.assertEqual([], result["finance_review"])
        send_private.assert_not_awaited()
        write_audit.assert_not_awaited()

    async def test_audit_lookup_exception_skips_only_that_alert(self):
        settlement = {
            "gap_rows": [
                [
                    "P0", "天猫", "天猫宝空", "2026-07", "采购成本",
                    "tm-order-001", "订单无法取得商家编码/外部系统编号",
                    "无法映射采购成本", "补订单明细商家编码或SKU对照表",
                ],
                [
                    "P0", "抖音", "抖音纷岚", "2026-07", "采购成本",
                    "FL-DOCK-001", "采购成本表未匹配或成本为0",
                    "毛利会虚高", "维护产品采购成本台后重跑",
                ],
                [
                    "P0", "小红书", "小红书纷岚", "2026-07", "采购成本",
                    "mystery-001", "成本口径冲突，系统无法判断责任",
                    "毛利暂不可确认", "由财务/系统核对",
                ],
            ],
            "cost_rows": [[
                "抖音", "抖音纷岚", "2026-07", "dy-order-001", "FL-DOCK-001",
                "FUNLAB Dock", 2, 0, 0, "成本缺失/为0",
            ]],
        }

        async def audit_exists(key):
            if ":operations:" in key:
                raise TimeoutError("审计表查询超时")
            return False

        with patch(
            "app.card_workflow._ops_group_mentions",
            new=AsyncMock(side_effect=AssertionError("审计查询失败后不应发送该运营卡")),
        ), patch.object(
            feishu,
            "send_interactive_open_id",
            new=AsyncMock(side_effect=AssertionError("不应发送采购或财务私聊")),
        ), patch.object(
            cost_gap_alert.ledger,
            "audit_exists",
            new=AsyncMock(side_effect=audit_exists),
        ), patch.object(
            cost_gap_alert.ledger,
            "write_audit",
            new=AsyncMock(),
        ):
            result = await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=False
            )

        self.assertEqual([], result["operations"])
        self.assertEqual([], result["procurement"])
        self.assertEqual([], result["finance_review"])

    async def test_operations_gap_sends_group_card_and_never_calls_procurement(self):
        settlement = {
            "gap_rows": [[
                "P0", "天猫", "天猫宝空", "2026-07", "采购成本",
                "5117032694368093324", "订单无法取得商家编码/外部系统编号",
                "无法映射采购成本", "补订单明细商家编码或SKU对照表",
            ]],
            "cost_rows": [[
                "天猫", "天猫宝空", "2026-07", "5117032694368093324", "",
                "POWKONG食人花底座", 1, 0, 0, "成本缺失/为0",
            ]],
        }
        with patch(
            "app.card_workflow._ops_group_mentions",
            new=AsyncMock(return_value={"ou_event_zhao": "赵伟俊"}),
        ), patch(
            "app.card_workflow._send_ops_card",
            new=AsyncMock(return_value=(["om_ops"], ["国内电商平台沟通群"], "group")),
        ) as send_ops, patch.object(
            feishu,
            "resolve_users_by_job_title",
            new=AsyncMock(side_effect=AssertionError("operations gap must not resolve procurement")),
        ), patch.object(
            cost_gap_alert.ledger,
            "audit_exists",
            new=AsyncMock(return_value=False),
        ), patch.object(
            cost_gap_alert.ledger,
            "write_audit",
            new=AsyncMock(),
        ):
            result = await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=False
            )

        self.assertEqual(["om_ops"], result["operations"])
        self.assertEqual([], result["procurement"])
        sent_card = send_ops.await_args.args[0]
        self.assertIn("<at id=ou_event_zhao></at>", str(sent_card))

    async def test_zero_cost_gap_sends_only_to_operations_group(self):
        settlement = {
            "gap_rows": [[
                "P0", "抖音", "抖音纷岚", "2026-07", "采购成本",
                "FL-DOCK-001", "采购成本表未匹配或成本为0",
                "毛利会虚高", "维护产品采购成本台后重跑",
            ]],
            "cost_rows": [[
                "抖音", "抖音纷岚", "2026-07", "order-001", "FL-DOCK-001",
                "FUNLAB Dock", 2, 0, 0, "成本缺失/为0",
            ]],
        }
        with patch(
            "app.card_workflow._ops_group_mentions",
            new=AsyncMock(return_value={"ou_event_zhao": "赵伟俊"}),
        ), patch(
            "app.card_workflow._send_ops_card",
            new=AsyncMock(return_value=(["om_ops"], ["国内电商平台沟通群"], "group")),
        ) as send_ops, patch.object(
            feishu,
            "resolve_users_by_job_title",
            new=AsyncMock(side_effect=AssertionError("不应解析采购岗位")),
        ) as resolve_proc, patch.object(
            feishu,
            "send_interactive_open_id",
            new=AsyncMock(side_effect=AssertionError("不应私聊采购")),
        ) as send_proc, patch.object(
            cost_gap_alert.ledger,
            "audit_exists",
            new=AsyncMock(return_value=False),
        ), patch.object(
            cost_gap_alert.ledger,
            "write_audit",
            new=AsyncMock(),
        ):
            result = await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=False
            )

        self.assertEqual(["om_ops"], result["operations"])
        self.assertEqual([], result["procurement"])
        resolve_proc.assert_not_awaited()
        send_proc.assert_not_awaited()
        self.assertIn("ERP SKU FL-DOCK-001", str(send_ops.await_args.args[0]))

    async def test_ambiguous_procurement_cost_gap_still_routes_to_operations(self):
        settlement = {
            "gap_rows": [[
                "P0", "天猫", "天猫纷岚", "2026-07", "采购成本",
                "mystery-001", "成本口径冲突，系统无法判断责任",
                "毛利暂不可确认", "由财务/系统核对",
            ]],
            "cost_rows": [],
        }
        with patch(
            "app.card_workflow._ops_group_mentions",
            new=AsyncMock(return_value={"ou_event_zhao": "赵伟俊"}),
        ), patch(
            "app.card_workflow._send_ops_card",
            new=AsyncMock(return_value=(["om_ops"], ["国内电商平台沟通群"], "group")),
        ) as send_ops, patch.object(
            feishu,
            "resolve_users_by_job_title",
            new=AsyncMock(side_effect=AssertionError("不应解析采购岗位")),
        ), patch.object(
            feishu,
            "send_interactive_open_id",
            new=AsyncMock(side_effect=AssertionError("不应私聊采购或财务")),
        ) as send_review, patch.object(
            cost_gap_alert.ledger,
            "audit_exists",
            new=AsyncMock(return_value=False),
        ), patch.object(
            cost_gap_alert.ledger,
            "write_audit",
            new=AsyncMock(),
        ):
            result = await cost_gap_alert.send_settlement_cost_gap_alerts(
                "2026-07", settlement, {}, frankie_only=False
            )

        self.assertEqual(["om_ops"], result["operations"])
        self.assertEqual([], result["finance_review"])
        send_review.assert_not_awaited()
        self.assertIn("待核实对象 mystery-001", str(send_ops.await_args.args[0]))


if __name__ == "__main__":
    unittest.main()
