"""Create the eight JudgeFlow demo business tables.

Revision ID: 0001_stage2
Revises:
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_stage2"
down_revision = None
branch_labels = None
depends_on = None


case_status = sa.Enum("NEW", "INVESTIGATING", "ARGUING", "ADJUDICATING", "DECIDED", name="case_status")
appeal_status = sa.Enum("NEW", "REVIEWING", "DECIDED", name="appeal_status")
evolution_status = sa.Enum(
    "NEW",
    "ATTRIBUTING",
    "DRAFTING",
    "REPLAYING",
    "AWAITING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "CLOSED",
    name="evolution_status",
)
work_order_status = sa.Enum("PENDING", "RUNNING", "SUCCEEDED", "FAILED", name="work_order_status")
aggregate_type = sa.Enum("CASE", "APPEAL", "POLICY_EVOLUTION", name="aggregate_type")
work_step_type = sa.Enum(
    "INVESTIGATION",
    "RISK_ARGUMENT",
    "COUNTER_ARGUMENT",
    "ADJUDICATION",
    "APPEAL_REVIEW",
    "ATTRIBUTION",
    "POLICY_DRAFTING",
    "REPLAY",
    name="work_step_type",
)
artifact_type = sa.Enum(
    "EVIDENCE_BUNDLE",
    "RISK_ARGUMENT",
    "COUNTER_ARGUMENT",
    "DECISION_RECORD",
    "APPEAL_DECISION",
    "ATTRIBUTION_REPORT",
    "POLICY_PROPOSAL",
    "REPLAY_REPORT",
    "HUMAN_APPROVAL",
    name="artifact_type",
)
producer_type = sa.Enum("AGENT", "HUMAN", "SYSTEM", name="producer_type")
policy_status = sa.Enum("BASELINE", "CANDIDATE", name="policy_status")
replay_recommendation = sa.Enum("PASS", "FAIL", "INCONCLUSIVE", name="replay_recommendation")
event_type = sa.Enum(
    "STATE_TRANSITION",
    "ARTIFACT_ACCEPTED",
    "WORK_ORDER_FAILED",
    "WORK_ORDER_RETRIED",
    "HUMAN_APPROVAL",
    "MCP_CALL",
    name="event_type",
)
domain_state = sa.Enum(
    "NEW",
    "INVESTIGATING",
    "ARGUING",
    "ADJUDICATING",
    "DECIDED",
    "REVIEWING",
    "ATTRIBUTING",
    "DRAFTING",
    "REPLAYING",
    "AWAITING_APPROVAL",
    "APPROVED",
    "REJECTED",
    "CLOSED",
    name="domain_state",
)


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("case_id", sa.String(128), primary_key=True),
        sa.Column("demo_run_id", sa.String(128), nullable=False),
        sa.Column("status", case_status, nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("input_json", postgresql.JSONB(), nullable=False),
        sa.Column("policy_snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column("policy_snapshot_hash", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state_version >= 0", name="ck_cases_state_version"),
    )
    op.create_index("ix_cases_demo_run_id", "cases", ["demo_run_id"])
    op.create_table(
        "appeals",
        sa.Column("appeal_id", sa.String(128), primary_key=True),
        sa.Column("case_id", sa.String(128), sa.ForeignKey("cases.case_id"), nullable=False),
        sa.Column("status", appeal_status, nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("request_json", postgresql.JSONB(), nullable=False),
        sa.Column("original_case_snapshot_json", postgresql.JSONB(), nullable=False),
        sa.Column("original_case_snapshot_hash", sa.String(71), nullable=False),
        sa.Column("allowed_evidence_ids", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state_version >= 0", name="ck_appeals_state_version"),
    )
    op.create_table(
        "policy_evolutions",
        sa.Column("evolution_id", sa.String(128), primary_key=True),
        sa.Column("source_appeal_id", sa.String(128), sa.ForeignKey("appeals.appeal_id"), nullable=False),
        sa.Column("status", evolution_status, nullable=False),
        sa.Column("state_version", sa.Integer(), nullable=False),
        sa.Column("base_policy_id", sa.String(128), nullable=False),
        sa.Column("base_policy_version", sa.String(128), nullable=False),
        sa.Column("current_proposal_artifact_id", sa.String(128)),
        sa.Column("current_replay_id", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state_version >= 0", name="ck_policy_evolutions_state_version"),
    )
    op.create_table(
        "work_orders",
        sa.Column("work_order_id", sa.String(128), primary_key=True),
        sa.Column("aggregate_type", aggregate_type, nullable=False),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("step_type", work_step_type, nullable=False),
        sa.Column("assignee", sa.String(128), nullable=False),
        sa.Column("expected_artifact_type", artifact_type, nullable=False),
        sa.Column("input_refs", postgresql.JSONB(), nullable=False),
        sa.Column("status", work_order_status, nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False, unique=True),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt >= 1", name="ck_work_orders_attempt"),
    )
    op.create_table(
        "artifacts",
        sa.Column("artifact_id", sa.String(128), primary_key=True),
        sa.Column("aggregate_type", aggregate_type, nullable=False),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("work_order_id", sa.String(128), sa.ForeignKey("work_orders.work_order_id"), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("producer_type", producer_type, nullable=False),
        sa.Column("producer_id", sa.String(128), nullable=False),
        sa.Column("artifact_type", artifact_type, nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("work_order_id", "run_id", "artifact_type", name="uq_artifacts_idempotency"),
    )
    op.create_table(
        "policies",
        sa.Column("policy_id", sa.String(128), primary_key=True),
        sa.Column("version", sa.String(128), primary_key=True),
        sa.Column("status", policy_status, nullable=False),
        sa.Column("title", sa.String(2000), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("dsl_json", postgresql.JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("source_proposal_artifact_id", sa.String(128), sa.ForeignKey("artifacts.artifact_id")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "replay_runs",
        sa.Column("replay_id", sa.String(128), primary_key=True),
        sa.Column(
            "evolution_id", sa.String(128), sa.ForeignKey("policy_evolutions.evolution_id"), nullable=False
        ),
        sa.Column("proposal_artifact_id", sa.String(128), sa.ForeignKey("artifacts.artifact_id"), nullable=False),
        sa.Column("baseline_policy_id", sa.String(128), nullable=False),
        sa.Column("baseline_policy_version", sa.String(128), nullable=False),
        sa.Column("candidate_policy_id", sa.String(128), nullable=False),
        sa.Column("candidate_policy_version", sa.String(128), nullable=False),
        sa.Column("dataset_version", sa.String(128), nullable=False),
        sa.Column("dataset_manifest_hash", sa.String(71), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(), nullable=False),
        sa.Column("case_results_json", postgresql.JSONB(), nullable=False),
        sa.Column("recommendation", replay_recommendation, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["baseline_policy_id", "baseline_policy_version"], ["policies.policy_id", "policies.version"]
        ),
        sa.ForeignKeyConstraint(
            ["candidate_policy_id", "candidate_policy_version"], ["policies.policy_id", "policies.version"]
        ),
    )
    op.create_table(
        "domain_events",
        sa.Column("event_id", sa.String(128), primary_key=True),
        sa.Column("event_type", event_type, nullable=False),
        sa.Column("aggregate_type", aggregate_type, nullable=False),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("from_state", domain_state),
        sa.Column("to_state", domain_state),
        sa.Column("work_order_id", sa.String(128), sa.ForeignKey("work_orders.work_order_id")),
        sa.Column("run_id", sa.String(128)),
        sa.Column("trace_id", sa.String(128), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("payload_hash", sa.String(71), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("domain_events")
    op.drop_table("replay_runs")
    op.drop_table("policies")
    op.drop_table("artifacts")
    op.drop_table("work_orders")
    op.drop_table("policy_evolutions")
    op.drop_table("appeals")
    op.drop_index("ix_cases_demo_run_id", table_name="cases")
    op.drop_table("cases")
    for enum in (
        event_type,
        domain_state,
        replay_recommendation,
        policy_status,
        producer_type,
        work_order_status,
        artifact_type,
        work_step_type,
        aggregate_type,
        evolution_status,
        appeal_status,
        case_status,
    ):
        enum.drop(op.get_bind(), checkfirst=True)
