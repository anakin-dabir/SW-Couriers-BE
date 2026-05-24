"""credit modules initial schema

Creates all tables, sequences, and indexes for the six credit modules
(org_credit, org_credit_alerts, org_credit_applications, org_credit_monitoring,
org_credit_reviews, org_credit_settings). This consolidates what was previously
spread across migrations 0073 through 0104.

Revision ID: 0080_credit_modules_initial
Revises: 0079_keep_legacy_susp_rule
Create Date: 2026-04-22

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0080_credit_modules_initial"
down_revision: str | None = "0079_keep_legacy_susp_rule"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.schema.CreateSequence(sa.Sequence("credit_app_seq")))
    op.execute(sa.schema.CreateSequence(sa.Sequence("credit_app_draft_seq")))

    op.create_table(
        "global_credit_account_cooldown_periods",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("months", sa.Integer(), nullable=True),
        sa.Column("days", sa.Integer(), nullable=True),
        sa.Column("hours", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "org_credit_account_cooldown_periods",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("months", sa.Integer(), nullable=False),
        sa.Column("days", sa.Integer(), nullable=False),
        sa.Column("hours", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_org_credit_cooldown_period_org_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_org_credit_account_cooldown_periods_organization_id"),
        "org_credit_account_cooldown_periods", ["organization_id"], unique=True,
    )

    op.create_table(
        "org_credit_cooldown_windows",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_months", sa.Integer(), nullable=False),
        sa.Column("policy_days", sa.Integer(), nullable=False),
        sa.Column("policy_hours", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_org_credit_cooldown_window_org_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_org_credit_cooldown_windows_organization_id"),
        "org_credit_cooldown_windows", ["organization_id"], unique=True,
    )

    op.create_table(
        "org_credit_reports",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("connect_id", sa.String(length=100), nullable=True),
        sa.Column("credit_score", sa.Integer(), nullable=True),
        sa.Column("credit_score_max", sa.Integer(), nullable=True),
        sa.Column("credit_rating", sa.String(length=10), nullable=True),
        sa.Column("credit_rating_description", sa.String(length=255), nullable=True),
        sa.Column("recommended_credit_limit", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("recommended_credit_limit_currency", sa.String(length=3), nullable=True),
        sa.Column("previous_credit_rating", sa.String(length=10), nullable=True),
        sa.Column("previous_rating_changed_at", sa.Date(), nullable=True),
        sa.Column("risk_band", sa.String(length=50), nullable=True),
        sa.Column("probability_of_default_12m", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("assessment_commentary", sa.Text(), nullable=True),
        sa.Column("company_name", sa.String(length=500), nullable=True),
        sa.Column("legal_entity_name", sa.String(length=500), nullable=True),
        sa.Column("company_status", sa.String(length=50), nullable=True),
        sa.Column("company_registration_number", sa.String(length=32), nullable=True),
        sa.Column("date_of_incorporation", sa.Date(), nullable=True),
        sa.Column("country", sa.String(length=10), nullable=True),
        sa.Column("latest_turnover", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("latest_turnover_currency", sa.String(length=3), nullable=True),
        sa.Column("registered_address", sa.Text(), nullable=True),
        sa.Column("industry_code", sa.String(length=20), nullable=True),
        sa.Column("industry_description", sa.String(length=255), nullable=True),
        sa.Column("vat_number", sa.String(length=32), nullable=True),
        sa.Column("contact_number", sa.String(length=40), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_by_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("directors", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("risk_indicators", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("payment_behaviour_description", sa.Text(), nullable=True),
        sa.Column("raw_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["checked_by_user_id"], ["users.id"],
            name="fk_credit_report_checked_by_user_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_credit_report_organization_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_org_credit_reports_organization_id"),
        "org_credit_reports", ["organization_id"], unique=True,
    )

    op.create_table(
        "org_credit_applications",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("application_number", sa.String(length=30), nullable=True),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "SUBMITTED", "REVIEWER_ASSIGNED", "REFERENCES_VERIFIED",
                "CREDIT_CHECK_COMPLETED", "CREDIT_CHECK_FAILED",
                "CREDIT_CHECK_INVESTIGATION_PROGRESS", "READY_FOR_DECISION",
                "APPROVED", "REJECTED", "WITHDRAWN", "CANCELLED",
                name="creditapplicationstatus", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "state",
            sa.Enum("DRAFT", "ACTIVE", name="creditapplicationlifecyclestate", native_enum=False),
            nullable=False,
        ),
        sa.Column("submitted_by_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("company_registration_number", sa.String(length=32), nullable=True),
        sa.Column("vat_registration_number", sa.String(length=32), nullable=True),
        sa.Column(
            "industry",
            sa.Enum(
                "AGRICULTURE_AND_FARMING", "AUTOMOTIVE", "CONSTRUCTION_AND_BUILDING",
                "EDUCATION", "ENERGY_AND_UTILITIES", "FINANCIAL_SERVICES",
                "FOOD_AND_BEVERAGE", "HEALTHCARE_AND_PHARMACEUTICALS",
                "HOME_AND_LIFESTYLE", "HOSPITALITY_AND_TOURISM",
                "IT_AND_TECHNOLOGY", "LOGISTICS_AND_TRANSPORT",
                "MANUFACTURING", "MEDIA_AND_ENTERTAINMENT",
                "PROFESSIONAL_SERVICES", "REAL_ESTATE", "RETAIL",
                "TELECOMMUNICATIONS", "WHOLESALE_AND_DISTRIBUTION", "OTHER",
                name="creditappindustry", native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column(
            "number_of_employees",
            sa.Enum(
                "1-10 employees", "11-50 employees", "51-200 employees",
                "201-500 employees", "501-1000 employees", "1000+ employees",
                name="creditappemployeerange", native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("date_of_incorporation", sa.Date(), nullable=True),
        sa.Column("years_trading", sa.Integer(), nullable=True),
        sa.Column("annual_turnover", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("net_profit", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("bank_name", sa.String(length=255), nullable=True),
        sa.Column("bank_sort_code", sa.String(length=12), nullable=True),
        sa.Column("bank_account_number_last4", sa.String(length=10), nullable=True),
        sa.Column(
            "bank_account_type",
            sa.Enum(
                "BUSINESS_CURRENT", "BUSINESS_SAVINGS", "OTHER",
                name="creditappbankaccounttype", native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("requested_credit_limit", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("requested_payment_terms_days", sa.Integer(), nullable=True),
        sa.Column("expected_monthly_spend", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("seasonal_peaks", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("justification", sa.Text(), nullable=True),
        sa.Column("director_signatory_name", sa.String(length=255), nullable=True),
        sa.Column("director_signatory_position", sa.String(length=120), nullable=True),
        sa.Column("declaration_date", sa.Date(), nullable=True),
        sa.Column("consent_credit_check", sa.Boolean(), nullable=False),
        sa.Column("consent_terms_and_conditions", sa.Boolean(), nullable=False),
        sa.Column("consent_data_processing", sa.Boolean(), nullable=False),
        sa.Column("assigned_reviewer_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewer_assigned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("references_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credit_check_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_credit_limit", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("approved_payment_terms_days", sa.Integer(), nullable=True),
        sa.Column(
            "review_frequency",
            sa.Enum("MONTHLY", "QUARTERLY", "ANNUALLY", name="creditappreviewfrequency", native_enum=False),
            nullable=True,
        ),
        sa.Column("approval_notes", sa.Text(), nullable=True),
        sa.Column(
            "rejection_category",
            sa.Enum(
                "INSUFFICIENT_REFERENCES", "POOR_FINANCIAL_STANDING",
                "INCOMPLETE_INFORMATION", "POLICY_NON_COMPLIANCE", "OTHER",
                name="creditapprejectioncategory", native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("cancellation_reason", sa.Text(), nullable=True),
        sa.Column("internal_notes", sa.Text(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_by_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_by_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("withdrawn_by_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(
            ["approved_by_user_id"], ["users.id"],
            name="fk_credit_app_approved_by_user_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_reviewer_user_id"], ["users.id"],
            name="fk_credit_app_assigned_reviewer_user_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["cancelled_by_user_id"], ["users.id"],
            name="fk_credit_app_cancelled_by_user_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_credit_app_organization_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["rejected_by_user_id"], ["users.id"],
            name="fk_credit_app_rejected_by_user_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"], ["users.id"],
            name="fk_credit_app_submitted_by_user_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["withdrawn_by_user_id"], ["users.id"],
            name="fk_credit_app_withdrawn_by_user_id", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_org_credit_applications_application_number"),
        "org_credit_applications", ["application_number"], unique=True,
    )
    op.create_index(
        op.f("ix_org_credit_applications_approved_by_user_id"),
        "org_credit_applications", ["approved_by_user_id"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_applications_assigned_reviewer_user_id"),
        "org_credit_applications", ["assigned_reviewer_user_id"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_applications_cancelled_by_user_id"),
        "org_credit_applications", ["cancelled_by_user_id"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_applications_deleted_at"),
        "org_credit_applications", ["deleted_at"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_applications_organization_id"),
        "org_credit_applications", ["organization_id"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_applications_rejected_by_user_id"),
        "org_credit_applications", ["rejected_by_user_id"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_applications_state"),
        "org_credit_applications", ["state"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_applications_withdrawn_by_user_id"),
        "org_credit_applications", ["withdrawn_by_user_id"], unique=False,
    )

    op.create_table(
        "org_credit_accounts",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "ON_HOLD", "SUSPENDED", "CLOSED", name="orgcreditaccountstatus", native_enum=False),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("action_by_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("last_status_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("credit_limit", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("credit_limit_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("pending_credit_limit", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("pending_credit_limit_effective_from", sa.Date(), nullable=True),
        sa.Column("payment_terms_days", sa.Integer(), nullable=True),
        sa.Column("pending_payment_terms_days", sa.Integer(), nullable=True),
        sa.Column("pending_payment_terms_effective_from", sa.Date(), nullable=True),
        sa.Column("payment_terms_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_terms_effective_from", sa.Date(), nullable=True),
        sa.Column("used_credit", sa.Numeric(precision=14, scale=2), server_default="0", nullable=False),
        sa.Column(
            "review_frequency",
            sa.Enum("MONTHLY", "QUARTERLY", "SEMI_ANNUAL", "ANNUAL", name="orgcreditreviewfrequency", native_enum=False),
            nullable=True,
        ),
        sa.Column("next_review_date", sa.Date(), nullable=True),
        sa.Column("last_review_date", sa.Date(), nullable=True),
        sa.Column(
            "review_reminder_period",
            sa.Enum("THREE_DAYS", "SEVEN_DAYS", "FOURTEEN_DAYS", name="creditreviewreminderperiod", native_enum=False),
            nullable=True,
        ),
        sa.Column("assigned_reviewer_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column(
            "review_risk_level",
            sa.Enum("LOW", "MEDIUM", "HIGH", "CRITICAL", name="creditreviewrisklevel", native_enum=False),
            nullable=True,
        ),
        sa.Column("hold_threshold_pct", sa.Integer(), nullable=True),
        sa.Column("credit_facility_start_date", sa.Date(), nullable=True),
        sa.Column("credit_facility_end_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["action_by_user_id"], ["users.id"],
            name="fk_org_credit_account_action_by_user_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_reviewer_user_id"], ["users.id"],
            name="fk_org_credit_account_reviewer_user_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_org_credit_account_organization_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_org_credit_accounts_organization_id"),
        "org_credit_accounts", ["organization_id"], unique=True,
    )

    op.create_table(
        "org_credit_alert_configs",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "alert_type",
            sa.Enum(
                "CREDIT_UTILISATION_MONITORING", "CREDIT_LIMIT_BREACH",
                "CREDIT_SCORE_DROP", "CREDIT_RATING_DOWNGRADE",
                "SCHEDULED_CREDIT_REVIEW_REMINDER", "REVIEW_OVERDUE",
                "LATE_PAYMENT_BEHAVIOUR", "CREDIT_FACILITY_EXPIRY_REMINDER",
                "CREDIT_FACILITY_EXPIRED", "ACCOUNT_ON_HOLD", "ACCOUNT_SUSPENDED",
                name="creditalerttype", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("warning_threshold_pct", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("critical_threshold_pct", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("threshold_value_int", sa.Integer(), nullable=True),
        sa.Column("threshold_days", sa.Integer(), nullable=True),
        sa.Column(
            "cooldown_period",
            sa.Enum("ONE_HOUR", "SEVEN_HOURS", "FOURTEEN_HOURS", "TWENTY_FOUR_HOURS", name="creditalertcooldownhours", native_enum=False),
            server_default="ONE_HOUR",
            nullable=False,
        ),
        sa.Column(
            "delivery_channel",
            sa.Enum("BOTH", "EMAIL_ONLY", "IN_APP_ONLY", name="creditalertdeliverychannel", native_enum=False),
            server_default="BOTH",
            nullable=False,
        ),
        sa.Column("auto_acknowledge", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_org_credit_alert_config_organization_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_org_credit_alert_configs_organization_id"),
        "org_credit_alert_configs", ["organization_id"], unique=False,
    )
    op.create_index(
        "uq_org_credit_alert_configs_org_type",
        "org_credit_alert_configs", ["organization_id", "alert_type"], unique=True,
    )

    op.create_table(
        "org_credit_alerts",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "alert_type",
            sa.Enum(
                "CREDIT_UTILISATION_MONITORING", "CREDIT_LIMIT_BREACH",
                "CREDIT_SCORE_DROP", "CREDIT_RATING_DOWNGRADE",
                "SCHEDULED_CREDIT_REVIEW_REMINDER", "REVIEW_OVERDUE",
                "LATE_PAYMENT_BEHAVIOUR", "CREDIT_FACILITY_EXPIRY_REMINDER",
                "CREDIT_FACILITY_EXPIRED", "ACCOUNT_ON_HOLD", "ACCOUNT_SUSPENDED",
                name="creditalerttype", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            sa.Enum("WARNING", "CRITICAL", name="creditalertseverity", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("ACTIVE", "SNOOZED", "ACKNOWLEDGED", "AUTO_ACKNOWLEDGED", "RESOLVED", name="creditalertstatus", native_enum=False),
            server_default="ACTIVE",
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("triggered_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("cooldown_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("snoozed_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_by_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["acknowledged_by_user_id"], ["users.id"],
            name="fk_org_credit_alert_acknowledged_by_user_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_org_credit_alert_organization_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_org_credit_alerts_org_triggered_at",
        "org_credit_alerts", ["organization_id", "triggered_at"], unique=False,
    )
    op.create_index(
        "ix_org_credit_alerts_org_type_status",
        "org_credit_alerts", ["organization_id", "alert_type", "status"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_alerts_organization_id"),
        "org_credit_alerts", ["organization_id"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_alerts_triggered_at"),
        "org_credit_alerts", ["triggered_at"], unique=False,
    )

    op.create_table(
        "org_credit_application_attachments",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("application_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "attachment_type",
            sa.Enum("BANK_REFERENCE", name="creditappattachmenttype", native_enum=False),
            nullable=False,
        ),
        sa.Column("r2_key", sa.String(length=500), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("uploaded_by", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"], ["org_credit_applications.id"],
            name="fk_credit_app_attachment_app_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_credit_app_attachment_org_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"],
            name="fk_credit_app_attachment_uploaded_by", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_org_credit_application_attachments_application_id"),
        "org_credit_application_attachments", ["application_id"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_application_attachments_organization_id"),
        "org_credit_application_attachments", ["organization_id"], unique=False,
    )

    op.create_table(
        "org_credit_application_drafts",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "draft_number",
            sa.String(length=20),
            server_default=sa.text("'CAD-' || lpad(nextval('credit_app_draft_seq')::text, 3, '0')"),
            nullable=False,
        ),
        sa.Column("application_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("created_by_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("published_by_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"], ["org_credit_applications.id"],
            name="fk_draft_application_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"],
            name="fk_draft_created_by_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["published_by_id"], ["users.id"],
            name="fk_draft_published_by_id", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_org_credit_application_drafts_application_id"),
        "org_credit_application_drafts", ["application_id"], unique=True,
    )
    op.create_index(
        op.f("ix_org_credit_application_drafts_draft_number"),
        "org_credit_application_drafts", ["draft_number"], unique=True,
    )

    op.create_table(
        "org_credit_application_trade_references",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("application_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("ref_index", sa.Integer(), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("contact_person", sa.String(length=255), nullable=True),
        sa.Column("contact_phone", sa.String(length=40), nullable=True),
        sa.Column("contact_email", sa.String(length=320), nullable=True),
        sa.Column("account_number_reference", sa.String(length=100), nullable=True),
        sa.Column("credit_limit_with_reference", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "relationship_duration",
            sa.Enum(
                "LESS_THAN_1_YEAR", "ONE_TO_TWO_YEARS", "TWO_TO_FIVE_YEARS",
                "FIVE_TO_TEN_YEARS", "OVER_TEN_YEARS",
                name="creditapprelationshipduration", native_enum=False,
            ),
            nullable=True,
        ),
        sa.Column(
            "verification_status",
            sa.Enum(
                "PENDING", "VERIFIED", "DECLINED", "UNABLE_TO_VERIFY",
                name="tradereferenceverificationstatus", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"], ["org_credit_applications.id"],
            name="fk_trade_ref_application_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_user_id"], ["users.id"],
            name="fk_trade_ref_verified_by_user_id", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_credit_app_trade_ref_app_idx",
        "org_credit_application_trade_references", ["application_id", "ref_index"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_application_trade_references_application_id"),
        "org_credit_application_trade_references", ["application_id"], unique=False,
    )

    op.create_table(
        "org_credit_investigations",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("application_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column(
            "status",
            sa.Enum("IN_PROGRESS", "COMPLETED", "FAILED", "CANCELLED", name="orgcreditinvestigationstatus", native_enum=False),
            server_default="IN_PROGRESS",
            nullable=False,
        ),
        sa.Column("reg_no", sa.String(length=64), nullable=True),
        sa.Column("company_name", sa.String(length=500), nullable=True),
        sa.Column("country", sa.String(length=10), nullable=True),
        sa.Column("provider_reference", sa.String(length=128), nullable=True),
        sa.Column("connect_id", sa.String(length=100), nullable=True),
        sa.Column("requested_by_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("raw_request", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("raw_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["application_id"], ["org_credit_applications.id"],
            name="fk_org_credit_investigation_application_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_org_credit_investigation_organization_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"],
            name="fk_org_credit_investigation_requested_by_user_id", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_org_credit_investigation_application_id",
        "org_credit_investigations", ["application_id"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_investigations_organization_id"),
        "org_credit_investigations", ["organization_id"], unique=False,
    )
    op.create_index(
        "uq_org_credit_investigation_active_per_org",
        "org_credit_investigations", ["organization_id"], unique=True,
        postgresql_where=sa.text("status = 'IN_PROGRESS'"),
    )

    op.create_table(
        "org_credit_ledger_entries",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("account_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "movement_type",
            sa.Enum("CONSUME", "REPAY", "MANUAL_ADJUST_USED", name="orgcreditledgermovementtype", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "source_type",
            sa.Enum("ORDER", "INVOICE", "PAYMENT", "MANUAL", "SYSTEM", name="orgcreditledgersourcetype", native_enum=False),
            nullable=True,
        ),
        sa.Column("source_id", sa.String(length=36), nullable=True),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("used_credit_after", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("available_credit_after", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("credit_limit_after", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column(
            "adjustment_reason",
            sa.Enum("COMPLIANCE", "GOODWILL", "DATA_CORRECTION", "OTHER", name="orgcreditadjustmentreason", native_enum=False),
            nullable=True,
        ),
        sa.Column("actor_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["org_credit_accounts.id"],
            name="fk_org_credit_ledger_account_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"],
            name="fk_org_credit_ledger_actor_user_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_org_credit_ledger_organization_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_org_credit_ledger_entries_account_id"),
        "org_credit_ledger_entries", ["account_id"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_ledger_entries_organization_id"),
        "org_credit_ledger_entries", ["organization_id"], unique=False,
    )
    op.create_index(
        "ix_org_credit_ledger_org_created",
        "org_credit_ledger_entries", ["organization_id", "created_at"], unique=False,
    )
    op.create_index(
        "uq_org_credit_ledger_idempotency",
        "org_credit_ledger_entries", ["organization_id", "idempotency_key"], unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )

    op.create_table(
        "org_credit_internal_score_history",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("credit_account_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=20), nullable=False),
        sa.Column("breakdown", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("calculated_by_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["calculated_by_user_id"], ["users.id"],
            name="fk_org_credit_score_hist_actor_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["credit_account_id"], ["org_credit_accounts.id"],
            name="fk_org_credit_score_hist_account_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_org_credit_score_hist_org_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_org_credit_internal_score_history_organization_id"),
        "org_credit_internal_score_history", ["organization_id"], unique=False,
    )
    op.create_index(
        "ix_org_credit_score_hist_org_created",
        "org_credit_internal_score_history", ["organization_id", "created_at"], unique=False,
    )

    op.create_table(
        "org_credit_status_history",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("credit_account_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column(
            "from_status",
            sa.Enum("ACTIVE", "ON_HOLD", "SUSPENDED", "CLOSED", name="orgcreditaccountstatus", native_enum=False),
            nullable=True,
        ),
        sa.Column(
            "to_status",
            sa.Enum("ACTIVE", "ON_HOLD", "SUSPENDED", "CLOSED", name="orgcreditaccountstatus", native_enum=False),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"],
            name="fk_org_credit_status_hist_actor_user_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["credit_account_id"], ["org_credit_accounts.id"],
            name="fk_org_credit_status_hist_account_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_org_credit_status_hist_org_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_org_credit_status_hist_account",
        "org_credit_status_history", ["credit_account_id"], unique=False,
    )
    op.create_index(
        "ix_org_credit_status_hist_org_created",
        "org_credit_status_history", ["organization_id", "created_at"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_status_history_organization_id"),
        "org_credit_status_history", ["organization_id"], unique=False,
    )

    op.create_table(
        "org_credit_limit_adjustment_history",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("credit_account_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("previous_limit", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("new_limit", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("reason_category", sa.String(length=128), nullable=False),
        sa.Column("justification", sa.Text(), nullable=False),
        sa.Column("modified_by_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column(
            "status",
            sa.Enum("SCHEDULED", "APPLIED", name="oclah_status", native_enum=False),
            server_default="APPLIED",
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["credit_account_id"], ["org_credit_accounts.id"],
            name="fk_oclah_acct_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["modified_by_user_id"], ["users.id"],
            name="fk_oclah_user_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_oclah_org_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_oclah_org_created",
        "org_credit_limit_adjustment_history", ["organization_id", "created_at"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_limit_adjustment_history_organization_id"),
        "org_credit_limit_adjustment_history", ["organization_id"], unique=False,
    )

    op.create_table(
        "org_credit_limit_increase_requests",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("previous_limit", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("requested_limit", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("approved_limit", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "APPROVED", "REJECTED", name="oclis_status", native_enum=False),
            server_default="PENDING",
            nullable=False,
        ),
        sa.Column("requested_by_user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("reviewed_by_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_oclis_org_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"], ["users.id"],
            name="fk_oclis_requested_by", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by_user_id"], ["users.id"],
            name="fk_oclis_reviewed_by", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_oclis_org_created",
        "org_credit_limit_increase_requests", ["organization_id", "created_at"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_limit_increase_requests_organization_id"),
        "org_credit_limit_increase_requests", ["organization_id"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_limit_increase_requests_requested_by_user_id"),
        "org_credit_limit_increase_requests", ["requested_by_user_id"], unique=False,
    )
    op.create_index(
        "uq_oclis_one_pending_per_org",
        "org_credit_limit_increase_requests", ["organization_id"], unique=True,
        postgresql_where=sa.text("status = 'PENDING'"),
    )

    op.create_table(
        "org_credit_reviews",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("account_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("reviewer_user_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("review_date", sa.Date(), nullable=False),
        sa.Column(
            "review_frequency_at_time",
            sa.Enum("MONTHLY", "QUARTERLY", "SEMI_ANNUAL", "ANNUAL", name="orgcreditreviewfrequency", native_enum=False),
            nullable=True,
        ),
        sa.Column(
            "risk_level",
            sa.Enum("LOW", "MEDIUM", "HIGH", "CRITICAL", name="creditreviewrisklevel", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "outcome",
            sa.Enum(
                "MAINTAIN_CURRENT_TERMS", "INCREASE_LIMIT", "DECREASE_LIMIT",
                "EXTEND_TERMS", "SHORTEN_TERMS", "SUSPEND_ACCOUNT",
                "CLOSE_ACCOUNT", "ESCALATE_TO_SENIOR_ADMIN",
                name="creditreviewoutcome", native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column(
            "next_review_frequency",
            sa.Enum("MONTHLY", "QUARTERLY", "SEMI_ANNUAL", "ANNUAL", name="orgcreditreviewfrequency", native_enum=False),
            nullable=True,
        ),
        sa.Column("recommended_new_limit", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("recommended_payment_terms_days", sa.Integer(), nullable=True),
        sa.Column("credit_report_snapshot_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["account_id"], ["org_credit_accounts.id"],
            name="fk_org_credit_review_account_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["credit_report_snapshot_id"], ["org_credit_reports.id"],
            name="fk_org_credit_review_report_snapshot_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_org_credit_review_organization_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_user_id"], ["users.id"],
            name="fk_org_credit_review_reviewer_user_id", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_org_credit_reviews_org_review_date",
        "org_credit_reviews", ["organization_id", "review_date"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_reviews_organization_id"),
        "org_credit_reviews", ["organization_id"], unique=False,
    )

    op.create_table(
        "org_credit_terms_modification_history",
        sa.Column("id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("organization_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("credit_account_id", sa.UUID(as_uuid=False), nullable=False),
        sa.Column("old_payment_terms", sa.Text(), nullable=True),
        sa.Column("new_payment_terms", sa.Text(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("modified_by_user_id", sa.UUID(as_uuid=False), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("applied_to_unpaid_invoices", sa.Boolean(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("SCHEDULED", "APPLIED", name="schedcreditsettingstatus", native_enum=False),
            server_default="APPLIED",
            nullable=False,
        ),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["credit_account_id"], ["org_credit_accounts.id"],
            name="fk_org_credit_terms_hist_acct_id", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["modified_by_user_id"], ["users.id"],
            name="fk_org_credit_terms_hist_user_id", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"],
            name="fk_org_credit_terms_hist_org_id", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_org_credit_terms_hist_org_created",
        "org_credit_terms_modification_history", ["organization_id", "created_at"], unique=False,
    )
    op.create_index(
        op.f("ix_org_credit_terms_modification_history_organization_id"),
        "org_credit_terms_modification_history", ["organization_id"], unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_org_credit_terms_modification_history_organization_id"),
        table_name="org_credit_terms_modification_history",
    )
    op.drop_index(
        "ix_org_credit_terms_hist_org_created",
        table_name="org_credit_terms_modification_history",
    )
    op.drop_table("org_credit_terms_modification_history")

    op.drop_index(
        op.f("ix_org_credit_reviews_organization_id"),
        table_name="org_credit_reviews",
    )
    op.drop_index(
        "ix_org_credit_reviews_org_review_date",
        table_name="org_credit_reviews",
    )
    op.drop_table("org_credit_reviews")

    op.drop_index(
        "uq_oclis_one_pending_per_org",
        table_name="org_credit_limit_increase_requests",
        postgresql_where=sa.text("status = 'PENDING'"),
    )
    op.drop_index(
        op.f("ix_org_credit_limit_increase_requests_requested_by_user_id"),
        table_name="org_credit_limit_increase_requests",
    )
    op.drop_index(
        op.f("ix_org_credit_limit_increase_requests_organization_id"),
        table_name="org_credit_limit_increase_requests",
    )
    op.drop_index(
        "ix_oclis_org_created",
        table_name="org_credit_limit_increase_requests",
    )
    op.drop_table("org_credit_limit_increase_requests")

    op.drop_index(
        op.f("ix_org_credit_limit_adjustment_history_organization_id"),
        table_name="org_credit_limit_adjustment_history",
    )
    op.drop_index(
        "ix_oclah_org_created",
        table_name="org_credit_limit_adjustment_history",
    )
    op.drop_table("org_credit_limit_adjustment_history")

    op.drop_index(
        op.f("ix_org_credit_status_history_organization_id"),
        table_name="org_credit_status_history",
    )
    op.drop_index(
        "ix_org_credit_status_hist_org_created",
        table_name="org_credit_status_history",
    )
    op.drop_index(
        "ix_org_credit_status_hist_account",
        table_name="org_credit_status_history",
    )
    op.drop_table("org_credit_status_history")

    op.drop_index(
        "ix_org_credit_score_hist_org_created",
        table_name="org_credit_internal_score_history",
    )
    op.drop_index(
        op.f("ix_org_credit_internal_score_history_organization_id"),
        table_name="org_credit_internal_score_history",
    )
    op.drop_table("org_credit_internal_score_history")

    op.drop_index(
        "uq_org_credit_ledger_idempotency",
        table_name="org_credit_ledger_entries",
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.drop_index(
        "ix_org_credit_ledger_org_created",
        table_name="org_credit_ledger_entries",
    )
    op.drop_index(
        op.f("ix_org_credit_ledger_entries_organization_id"),
        table_name="org_credit_ledger_entries",
    )
    op.drop_index(
        op.f("ix_org_credit_ledger_entries_account_id"),
        table_name="org_credit_ledger_entries",
    )
    op.drop_table("org_credit_ledger_entries")

    op.drop_index(
        "uq_org_credit_investigation_active_per_org",
        table_name="org_credit_investigations",
        postgresql_where=sa.text("status = 'IN_PROGRESS'"),
    )
    op.drop_index(
        op.f("ix_org_credit_investigations_organization_id"),
        table_name="org_credit_investigations",
    )
    op.drop_index(
        "ix_org_credit_investigation_application_id",
        table_name="org_credit_investigations",
    )
    op.drop_table("org_credit_investigations")

    op.drop_index(
        op.f("ix_org_credit_application_trade_references_application_id"),
        table_name="org_credit_application_trade_references",
    )
    op.drop_index(
        "ix_credit_app_trade_ref_app_idx",
        table_name="org_credit_application_trade_references",
    )
    op.drop_table("org_credit_application_trade_references")

    op.drop_index(
        op.f("ix_org_credit_application_drafts_draft_number"),
        table_name="org_credit_application_drafts",
    )
    op.drop_index(
        op.f("ix_org_credit_application_drafts_application_id"),
        table_name="org_credit_application_drafts",
    )
    op.drop_table("org_credit_application_drafts")

    op.drop_index(
        op.f("ix_org_credit_application_attachments_organization_id"),
        table_name="org_credit_application_attachments",
    )
    op.drop_index(
        op.f("ix_org_credit_application_attachments_application_id"),
        table_name="org_credit_application_attachments",
    )
    op.drop_table("org_credit_application_attachments")

    op.drop_index(op.f("ix_org_credit_alerts_triggered_at"), table_name="org_credit_alerts")
    op.drop_index(op.f("ix_org_credit_alerts_organization_id"), table_name="org_credit_alerts")
    op.drop_index("ix_org_credit_alerts_org_type_status", table_name="org_credit_alerts")
    op.drop_index("ix_org_credit_alerts_org_triggered_at", table_name="org_credit_alerts")
    op.drop_table("org_credit_alerts")

    op.drop_index("uq_org_credit_alert_configs_org_type", table_name="org_credit_alert_configs")
    op.drop_index(
        op.f("ix_org_credit_alert_configs_organization_id"),
        table_name="org_credit_alert_configs",
    )
    op.drop_table("org_credit_alert_configs")

    op.drop_index(op.f("ix_org_credit_accounts_organization_id"), table_name="org_credit_accounts")
    op.drop_table("org_credit_accounts")

    op.drop_index(op.f("ix_org_credit_applications_withdrawn_by_user_id"), table_name="org_credit_applications")
    op.drop_index(op.f("ix_org_credit_applications_state"), table_name="org_credit_applications")
    op.drop_index(op.f("ix_org_credit_applications_rejected_by_user_id"), table_name="org_credit_applications")
    op.drop_index(op.f("ix_org_credit_applications_organization_id"), table_name="org_credit_applications")
    op.drop_index(op.f("ix_org_credit_applications_deleted_at"), table_name="org_credit_applications")
    op.drop_index(op.f("ix_org_credit_applications_cancelled_by_user_id"), table_name="org_credit_applications")
    op.drop_index(op.f("ix_org_credit_applications_assigned_reviewer_user_id"), table_name="org_credit_applications")
    op.drop_index(op.f("ix_org_credit_applications_approved_by_user_id"), table_name="org_credit_applications")
    op.drop_index(op.f("ix_org_credit_applications_application_number"), table_name="org_credit_applications")
    op.drop_table("org_credit_applications")

    op.drop_index(op.f("ix_org_credit_reports_organization_id"), table_name="org_credit_reports")
    op.drop_table("org_credit_reports")

    op.drop_index(
        op.f("ix_org_credit_cooldown_windows_organization_id"),
        table_name="org_credit_cooldown_windows",
    )
    op.drop_table("org_credit_cooldown_windows")

    op.drop_index(
        op.f("ix_org_credit_account_cooldown_periods_organization_id"),
        table_name="org_credit_account_cooldown_periods",
    )
    op.drop_table("org_credit_account_cooldown_periods")

    op.drop_table("global_credit_account_cooldown_periods")

    op.execute(sa.schema.DropSequence(sa.Sequence("credit_app_draft_seq")))
    op.execute(sa.schema.DropSequence(sa.Sequence("credit_app_seq")))
