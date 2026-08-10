import json
import shutil
import subprocess
import unittest
from pathlib import Path


MONEY_JS = Path(__file__).resolve().parents[1] / "app" / "static" / "money.js"


class ClientMoneyFormattingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required for client formatter tests")
    def test_client_formatter_uses_metadata_locale_and_zero_decimals(self):
        config = {
            "locale": "es-AR",
            "defaultCurrency": "BRL",
            "currencies": [
                {"code": "ARS", "symbol": "$", "decimal_places": 2},
                {"code": "USD", "symbol": "US$", "decimal_places": 2},
                {"code": "BRL", "symbol": "R$", "decimal_places": 2},
                {"code": "EUR", "symbol": "€", "decimal_places": 2},
                {"code": "CLP", "symbol": "CLP$", "decimal_places": 0},
            ],
        }
        script = f"""
global.window = {{MANGOTECA_MONEY: {json.dumps(config)}}};
require({json.dumps(str(MONEY_JS))});
console.log(JSON.stringify([
  window.MoneyFormat.formatAmount(5580.5, 'USD'),
  window.MoneyFormat.formatAmount(5580, 'USD'),
  window.MoneyFormat.formatAmount(1234.5, 'CLP'),
  window.MoneyFormat.formatCompactAmount(1200000, 'EUR'),
  window.MoneyFormat.periodCurrencyOrder(['EUR', 'ARS'])
]));
"""

        result = subprocess.run(
            ["node", "-e", script], check=True, capture_output=True, text=True
        )

        self.assertEqual(
            json.loads(result.stdout),
            ["US$ 5.580,50", "US$ 5.580", "CLP$ 1.235", "€ 1.2M", ["BRL", "ARS", "EUR"]],
        )


if __name__ == "__main__":
    unittest.main()
