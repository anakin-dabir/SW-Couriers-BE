"""OpenAPI docs snippets for Suspension Rules v1 API."""

from typing import Any

SUSP_RULES_LIST: dict[str, Any] = {
    "summary": "List suspension rules",
    "description": (
        "List account suspension rules configured in admin settings. Admin-only. "
        "Rules are evaluated once per day by a background job for CUSTOMER_B2B accounts. "
        "Filters `status` and `primary_trigger` support repeated list keys "
        "(e.g. status=ACTIVE&status=INACTIVE)."
    ),
}

SUSP_RULES_CREATE: dict[str, Any] = {
    "summary": "Create suspension rule",
    "description": "Create a new account suspension rule. Admin-only.",
    "openapi_extra": {
        "requestBody": {
            "content": {
                "application/json": {
                    "example": {
                        "name": "Strict Overdue Policy",
                        "condition_summary": "Overdue > 30 days and Outstanding > £5,000",
                        "logic": "AND",
                        "status": "ACTIVE",
                        "notes": "For high risk accounts",
                        "primary_trigger": "OVERDUE_DAYS_AND_AMOUNT",
                        "overdue_days_threshold": 30,
                        "overdue_amount_threshold": 5000.0,
                        "suspension_type": "AFTER_GRACE_PERIOD",
                        "grace_period_days": 7,
                        "notify_finance_team": True,
                        "send_warning_to_user": True,
                        "additional_conditions": {
                            "conditions": [
                                {
                                    "metric": "credit_utilisation_percent",
                                    "operator": ">=",
                                    "threshold": 90,
                                }
                            ]
                        },
                    }
                }
            }
        }
    },
}

SUSP_RULES_GET: dict[str, Any] = {
    "summary": "Get suspension rule",
    "description": "Get a single suspension rule by ID. Admin-only.",
}

SUSP_RULES_UPDATE: dict[str, Any] = {
    "summary": "Update suspension rule",
    "description": (
        "Update an existing suspension rule. Admin-only. "
        "Pass the latest `version` from the GET/list response for optimistic locking; "
        "the update will be rejected with 409 if the rule was modified concurrently."
    ),
    "openapi_extra": {
        "requestBody": {
            "content": {
                "application/json": {
                    "example": {
                        "name": "Strict Overdue Policy (Updated)",
                        "status": "INACTIVE",
                        "notes": "Temporarily disabled during pilot.",
                        "version": 3,
                    }
                }
            }
        }
    },
}

SUSP_RULES_DELETE: dict[str, Any] = {
    "summary": "Delete suspension rule",
    "description": "Delete a suspension rule. Admin-only. Prefer inactivating rules in production.",
}

SUSP_ACTIVITY_LIST: dict[str, Any] = {
    "summary": "List suspension activity",
    "description": ("View recent suspension activity (audit trail) showing which rules were triggered, for which accounts, " "and what actions were taken. Admin-only."),
}

SUSP_V2_RULESETS_LIST: dict[str, Any] = {
    "summary": "List suspension rule sets",
    "description": (
        "List suspension rule sets with scope/type filtering. "
        "Supports GLOBAL defaults and ORG overrides. Admin-only."
    ),
}

SUSP_V2_RULESETS_CREATE: dict[str, Any] = {
    "summary": "Create suspension rule set",
    "description": (
        "Create a rule set with ordered condition rows, AND/OR connectors, and action toggles. "
        "Duplicate condition types are rejected. "
        "Evaluation uses AND precedence over OR based on row order (no arbitrary parenthesis grouping). "
        "Runtime enforcement: pause_new_bookings blocks booking creation, restrict_portal_login blocks CUSTOMER_B2B auth "
        "for suspended organizations, and auto_suspension_enabled suspends CUSTOMER_B2B users."
    ),
}

SUSP_V2_RULESETS_GET: dict[str, Any] = {
    "summary": "Get suspension rule set",
    "description": "Get a single rule set by id. Admin-only.",
}

SUSP_V2_RULESETS_UPDATE: dict[str, Any] = {
    "summary": "Update suspension rule set",
    "description": (
        "Patch a rule set and optionally replace condition rows. "
        "Pass latest version for optimistic locking. "
        "Condition row order and connectors determine precedence (AND before OR). "
        "Action toggles are enforced at runtime by auth and booking service boundaries."
    ),
}

SUSP_V2_RULESETS_DELETE: dict[str, Any] = {
    "summary": "Delete suspension rule set",
    "description": "Delete a rule set. Admin-only.",
}

SUSP_V2_EFFECTIVE_RULESETS: dict[str, Any] = {
    "summary": "Get effective rule sets for organization",
    "description": (
        "Resolve effective rules for an organization with rule-state overlay. "
        "DEFAULT = active global rule, NEW = active org rule with no parent global link, "
        "CUSTOMISED = active org rule linked to a specific global default via parent_global_rule_set_id. "
        "Only linked defaults are suppressed by customised rules; NEW rules do not hide defaults."
    ),
}

SUSP_V2_APPLICABLE_RULESETS: dict[str, Any] = {
    "summary": "Get applicable rule sets for organization (ACTIVE + INACTIVE inventory)",
    "description": (
        "ORG rows for this organization (including INACTIVE) plus GLOBAL templates as DEFAULT rows, "
        "except DEFAULT rows are omitted when an ACTIVE CUSTOMISED org rule exists for that global "
        "(the customised row represents the template). "
        "Same DEFAULT / CUSTOMISED / NEW metadata shape as GET effective-rule-sets where rows appear. "
        "Each row includes **is_effective_for_org**: true only when that physical rule_set row "
        "is part of the resolved effective evaluation set (ACTIVE overlay semantics)."
    ),
}

SUSP_V2_ACTIVITY_LIST: dict[str, Any] = {
    "summary": "List enriched suspension activity",
    "description": (
        "List suspension activity with organization, rule_type, payment_model, and "
        "client context suitable for FE audit table rendering. "
        "Use rule_set_id for canonical filtering; rule_id is a backward-compatible alias."
    ),
}

SUSP_V2_RISK_EVENTS_CREATE: dict[str, Any] = {
    "summary": "Create payment risk event",
    "description": (
        "Create a payment risk signal used by suspension metrics "
        "(PAYMENT_FAILED, RETRY_FAILED, PAYMENT_SUCCESS, CHARGEBACK)."
    ),
}

SUSP_V2_ORG_OVERRIDE_UPSERT: dict[str, Any] = {
    "summary": "Upsert organization override rule",
    "description": (
        "Create or update an organization-specific override for a given rule_type. "
        "If an override does not exist, values are cloned from effective rules and merged with provided fields. "
        "In multi-ruleset mode, upsert updates the most recently updated ORG rule for the given organization + rule_type. "
        "ORG rules of that type replace GLOBAL rules for that organization in effective resolution. "
        "notify_account_manager delivers to account manager first, then ACCOUNT_OWNER contacts, then FINANCE_TEAM_EMAIL."
    ),
}

SUSP_V2_ORG_CUSTOMISE_GLOBAL: dict[str, Any] = {
    "summary": "Create customised org rule from global default",
    "description": (
        "Creates an ORG-scoped customised rule by editing a specific GLOBAL default rule. "
        "The global default is never mutated. Effective resolution hides only that linked default, "
        "while other defaults of the same type remain visible unless customised separately."
    ),
}

SUSP_V2_ORG_RULE_STATUS_UPDATE: dict[str, Any] = {
    "summary": "Toggle org rule active status",
    "description": (
        "Updates ACTIVE/INACTIVE for an ORG-scoped rule in client B2B settings. "
        "GLOBAL rules cannot be toggled via this endpoint."
    ),
}

SUSP_V2_ORG_RULE_RESTORE_DEFAULT: dict[str, Any] = {
    "summary": "Restore default for customised org rule",
    "description": (
        "Restores linked default behavior by deleting the customised ORG rule. "
        "Only valid for customised rules linked to a GLOBAL parent. "
        "Returns the restored GLOBAL default rule."
    ),
}

SUSP_V2_ORG_GLOBAL_SUPPRESSION_LIST: dict[str, Any] = {
    "summary": "List per-org global rule suppressions",
    "description": (
        "Returns GLOBAL suspension rule-set ids that are opted out for this organisation "
        "(they do not appear as DEFAULT in GET effective-rule-sets)."
    ),
}

SUSP_V2_ORG_GLOBAL_SUPPRESSION_PUT: dict[str, Any] = {
    "summary": "Set per-org suppression for a global suspension rule",
    "description": (
        "When suppressed=true, the GLOBAL rule template is excluded from effective DEFAULT rows for this org "
        "without modifying the shared GLOBAL row. suppressed=false removes the opt-out. "
        "Require global_rule_set_id to reference a GLOBAL scope rule."
    ),
}
