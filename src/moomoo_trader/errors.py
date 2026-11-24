class MoomooTraderError(Exception):
    """このパッケージで扱う業務エラーの基底例外。"""


class SdkNotInstalledError(MoomooTraderError):
    """moomoo/futu SDK が見つからない。"""


class OpenDConnectionError(MoomooTraderError):
    """OpenD との接続や通信に失敗した。"""


class ApiCallError(MoomooTraderError):
    """SDK 呼び出し自体は成功したが API がエラーを返した。"""


class DataNotReadyError(ApiCallError):
    """API は応答したが、口座データがまだ準備できていない。

    空データ（保有なし・注文なし）と区別するため、空配列として扱ってはならない。
    """


class OrderResultUnknownError(MoomooTraderError):
    """発注要求は送信されたが、証券側で受理されたか確認できない。

    二重発注を避けるため、照合が完了するまで同じ注文意図を再送してはならない。
    """


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
