class MoomooTraderError(Exception):
    """このパッケージで扱う業務エラーの基底例外。"""


class SdkNotInstalledError(MoomooTraderError):
    """moomoo/futu SDK が見つからない。"""


class OpenDConnectionError(MoomooTraderError):
    """OpenD との接続や通信に失敗した。"""


class ApiCallError(MoomooTraderError):
    """SDK 呼び出し自体は成功したが API がエラーを返した。"""


class AccountSelectionError(MoomooTraderError):
    """対象口座を一意に選べなかった。"""


class InvalidConfigError(MoomooTraderError):
    """設定ファイルの形式または値が不正。"""


class CredentialStoreError(MoomooTraderError):
    """Windows 資格情報ストアから秘密情報を取得できない。"""


class StrategyLoadError(MoomooTraderError):
    """戦略クラスの読み込みに失敗した。"""


class RiskRejectedError(MoomooTraderError):
    """リスクゲートによって注文を拒否した。"""
