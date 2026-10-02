"""Create persistent trade artifact and runtime state tables."""

from alembic import op
import sqlalchemy as sa

revision = "20261002_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "trade_artifacts" not in inspector.get_table_names():
        op.create_table(
            "trade_artifacts",
            sa.Column("trade_id", sa.Text(), primary_key=True),
            sa.Column("agent_address", sa.Text()),
            sa.Column("token_in", sa.Text()),
            sa.Column("token_out", sa.Text()),
            sa.Column("amount_in_usd", sa.Float()),
            sa.Column("leverage", sa.Float()),
            sa.Column("risk_score", sa.Float()),
            sa.Column("decision", sa.Text()),
            sa.Column("violations_json", sa.Text()),
            sa.Column("var_pct", sa.Float()),
            sa.Column("volatility_pct", sa.Float()),
            sa.Column("position_size_pct", sa.Float()),
            sa.Column("circuit_breaker", sa.Integer()),
            sa.Column("signal_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("risk_checks_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("intent_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("onchain_status", sa.Text(), nullable=False, server_default="not_requested"),
            sa.Column("onchain_tx_hash", sa.Text(), nullable=False, server_default=""),
            sa.Column("signature", sa.Text()),
            sa.Column("timestamp", sa.Text()),
            sa.Column("hash_ref", sa.Text()),
        )
    else:
        existing = {column["name"] for column in inspector.get_columns("trade_artifacts")}
        additions = (
            ("signal_json", sa.Text(), "{}"),
            ("risk_checks_json", sa.Text(), "[]"),
            ("intent_json", sa.Text(), "{}"),
            ("onchain_status", sa.Text(), "not_requested"),
            ("onchain_tx_hash", sa.Text(), ""),
        )
        for name, type_, default in additions:
            if name not in existing:
                op.add_column("trade_artifacts", sa.Column(
                    name, type_, nullable=False, server_default=default))

    if "runtime_state" not in inspector.get_table_names():
        op.create_table(
            "runtime_state",
            sa.Column("state_key", sa.Text(), primary_key=True),
            sa.Column("state_json", sa.Text(), nullable=False),
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "runtime_state" in inspector.get_table_names():
        op.drop_table("runtime_state")
    if "trade_artifacts" in inspector.get_table_names():
        existing = {column["name"] for column in inspector.get_columns("trade_artifacts")}
        for column in ("onchain_tx_hash", "onchain_status", "intent_json", "risk_checks_json", "signal_json"):
            if column in existing:
                op.drop_column("trade_artifacts", column)
