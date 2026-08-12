import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import writer


class SpreadsheetCreationTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_report_spreadsheet_surfaces_feishu_api_error(self):
        response = {"code": 99991663, "msg": "tenant access token invalid"}

        with patch.object(
            writer.feishu,
            "sheets_create",
            new=AsyncMock(return_value=response),
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                r"创建飞书毛利报表失败.*99991663.*tenant access token invalid",
            ):
                await writer.create_report_spreadsheet("2026-07")


if __name__ == "__main__":
    unittest.main()
