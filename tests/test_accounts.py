import unittest

from moomoo_trader.accounts import select_account
from moomoo_trader.errors import AccountSelectionError


class SelectAccountTest(unittest.TestCase):
    def test_account_id_が一致した口座を返す(self):
        accounts = [
            {"acc_id": "100", "trd_env": "REAL"},
            {"acc_id": "200", "trd_env": "SIMULATE"},
        ]

        selected = select_account(accounts, requested_env="REAL", account_id="100")

        self.assertEqual(selected["acc_id"], "100")

    def test_候補が一件なら自動選択する(self):
        accounts = [
            {"acc_id": "100", "trd_env": "REAL"},
            {"acc_id": "200", "trd_env": "SIMULATE"},
        ]

        selected = select_account(accounts, requested_env="SIMULATE")

        self.assertEqual(selected["acc_id"], "200")

    def test_候補が複数ならエラーにする(self):
        accounts = [
            {"acc_id": "100", "trd_env": "REAL"},
            {"acc_id": "101", "trd_env": "REAL"},
        ]

        with self.assertRaises(AccountSelectionError) as context:
            select_account(accounts, requested_env="REAL")

        self.assertIn("--account-id", str(context.exception))

    def test_候補がなければエラーにする(self):
        accounts = [
            {"acc_id": "100", "trd_env": "REAL"},
        ]

        with self.assertRaises(AccountSelectionError) as context:
            select_account(accounts, requested_env="SIMULATE")

        self.assertIn("SIMULATE", str(context.exception))


if __name__ == "__main__":
    unittest.main()
