import unittest
from unittest.mock import patch

from moomoo_trader.credentials import get_trade_password
from moomoo_trader.errors import CredentialStoreError


class CredentialStoreTest(unittest.TestCase):
    def test_資格情報を取得できる(self):
        with patch("keyring.get_password", return_value="mock-password-value"):
            password = get_trade_password("main-live")

        self.assertEqual("mock-password-value", password)

    def test_資格情報が見つからないとエラーになる(self):
        with patch("keyring.get_password", return_value=None):
            with self.assertRaises(CredentialStoreError):
                get_trade_password("missing")


if __name__ == "__main__":
    unittest.main()
