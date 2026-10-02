import pytest

from services.risk_engine import RiskConfig, RiskDecision, RiskEngine


@pytest.fixture
def engine():
    instance = RiskEngine(RiskConfig())
    instance.initialize(10_000)
    return instance


def test_position_limit_is_inclusive_at_limit(engine):
    assert engine.validate_trade_intent(1_000).decision == RiskDecision.APPROVE
    assessment = engine.validate_trade_intent(1_001)
    assert assessment.decision == RiskDecision.REJECT
    assert "MAX_POSITION_SIZE" in {item.rule for item in assessment.violations}


def test_leverage_limit_rejects_above_cap(engine):
    assessment = engine.validate_trade_intent(100, leverage=3.01)
    assert assessment.decision == RiskDecision.REJECT
    assert "LEVERAGE_CAP" in {item.rule for item in assessment.violations}


def test_circuit_breaker_blocks_trades(engine):
    engine.circuit_breaker_active = True
    assessment = engine.validate_trade_intent(100)
    assert assessment.decision == RiskDecision.REJECT
    assert "CIRCUIT_BREAKER" in {item.rule for item in assessment.violations}


def test_var_is_included_in_risk_score(engine):
    calm = engine.validate_trade_intent(100, asset_returns=[0.0001] * 30)
    volatile_returns = [(-0.08 if index % 2 else 0.08) for index in range(30)]
    volatile = engine.validate_trade_intent(100, asset_returns=volatile_returns)
    assert volatile.var_pct > calm.var_pct
    assert volatile.risk_score > calm.risk_score
