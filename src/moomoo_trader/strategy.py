from __future__ import annotations

import importlib
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Dict, List

from .errors import StrategyLoadError
from .models import MarketSnapshot, PortfolioState, TradeIntent


class Strategy(ABC):
    def __init__(self, strategy_id: str) -> None:
        self.strategy_id = strategy_id

    @abstractmethod
    def evaluate(
        self,
        snapshots: Dict[str, MarketSnapshot],
        portfolio_state: PortfolioState,
        clock: datetime,
    ) -> List[TradeIntent]:
        raise NotImplementedError


class NoOpStrategy(Strategy):
    def evaluate(
        self,
        snapshots: Dict[str, MarketSnapshot],
        portfolio_state: PortfolioState,
        clock: datetime,
    ) -> List[TradeIntent]:
        return []


def load_strategy(strategy_name: str, strategy_options: Dict[str, object]) -> Strategy:
    normalized = strategy_name.strip()
    if not normalized:
        raise StrategyLoadError("`strategy` が空です。")

    if normalized.lower() == "noop":
        strategy_id = str(strategy_options.get("strategy_id") or "noop")
        return NoOpStrategy(strategy_id=strategy_id)

    if ":" not in normalized:
        raise StrategyLoadError(
            "`strategy` は `noop` または `module.path:ClassName` 形式で指定してください。"
        )

    module_name, class_name = normalized.split(":", 1)
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise StrategyLoadError(
            "戦略モジュールを読み込めませんでした: {}".format(module_name)
        ) from exc

    strategy_class = getattr(module, class_name, None)
    if strategy_class is None:
        raise StrategyLoadError("戦略クラスが見つかりません: {}".format(class_name))

    instance = strategy_class(**strategy_options)
    if not isinstance(instance, Strategy):
        raise StrategyLoadError("戦略クラスは Strategy を継承してください。")
    return instance
