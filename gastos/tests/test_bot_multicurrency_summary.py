import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


APP_DIR = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP_DIR))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bot  # noqa: E402
import db  # noqa: E402
from support import reset_database  # noqa: E402


class BotMultiCurrencySummaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        reset_database({"123": "Tester"})
        self.user = db.get_user_by_telegram_id("123")

    async def test_monthly_summary_includes_eur_and_empty_family_default_first(self):
        db.set_family_default_currency("BRL")
        now = datetime.now()
        db.create_expense_full(
            self.user["id"], None, "Europa", 42,
            f"{now.year:04d}-{now.month:02d}-01", currency="EUR",
        )
        reply_text = AsyncMock()
        update = SimpleNamespace(message=SimpleNamespace(reply_text=reply_text))

        with patch.object(bot, "_get_authorized_user", AsyncMock(return_value=self.user)):
            await bot.cmd_gastos(update, SimpleNamespace())

        message = reply_text.await_args.args[0]
        self.assertIn("R$ 0", message)
        self.assertIn("€ 42", message)
        self.assertLess(message.index("R$ 0"), message.index("€ 42"))
        self.assertNotIn("US$", message)


if __name__ == "__main__":
    unittest.main()
