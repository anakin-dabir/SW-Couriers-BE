from __future__ import annotations

from app.core.swagger import create_doc_entry, custom_entry, error_401_entry, error_entry, success_entry

_TRADE_REFERENCES_EXAMPLE = [
    {
        "id": "ref-uuid-1",
        "ref_index": 0,
        "company_name": "Quick Logistics Co",
        "contact_person": "Jenny Wilson",
        "contact_phone": "+44154-48738374",
        "contact_email": "jennywilson@quicklogistics.co.uk",
        "account_number_reference": "ACC-10234",
        "credit_limit_with_reference": "10000.00",
        "relationship_duration": "2_TO_5_YEARS",
        "verification_status": "PENDING",
        "verified_at": None,
        "verified_by_user_id": None,
        "created_at": "2026-03-10T08:00:00Z",
        "updated_at": "2026-03-10T08:00:00Z",
    },
    {
        "id": "ref-uuid-2",
        "ref_index": 1,
        "company_name": "QuickSupply Co",
        "contact_person": "Sam Lee",
        "contact_phone": "+44 20 7946 0000",
        "contact_email": "accounts@quicksupply.co.uk",
        "account_number_reference": None,
        "credit_limit_with_reference": None,
        "relationship_duration": "1_TO_2_YEARS",
        "verification_status": "PENDING",
        "verified_at": None,
        "verified_by_user_id": None,
        "created_at": "2026-03-10T08:00:00Z",
        "updated_at": "2026-03-10T08:00:00Z",
    },
]

_BANK_REFERENCE_EXAMPLE = {
    "bank_name": "HSBC UK",
    "bank_sort_code": "40-11-22",
    "bank_account_number_last4": "7890",
    "bank_account_type": "BUSINESS_SAVINGS",
    "reference_letter": {
        "id": "bank-ref-uuid",
        "url": "https://example-r2-url",
        "filename": "bank-reference-letter.pdf",
    },
}

_CREDIT_REPORT_EXAMPLE = {
    "id": "credit-report-uuid",
    "connect_id": "CS-GB-08934567",
    "score": {
        "recommended_credit_limit": "25000.00",
        "recommended_credit_limit_currency": "GBP",
        "credit_rating": "B",
        "credit_score": 72,
        "credit_score_max": 100,
        "rating_description": "Good credit limit",
        "previous_credit_rating": "C",
        "previous_rating_changed_at": "2025-06-15",
        "risk_band": "Moderate Risk",
        "probability_of_default_12m": "1.80",
        "assessment_commentary": (
            "The company demonstrates stable financial performance with low risk indicators. "
            "Suitable for moderate credit exposure."
        ),
    },
    "risk_indicators": [
        {
            "key": "insolvency",
            "label": "Insolvency",
            "severity": "OK",
            "description": "No active insolvency proceedings",
            "details": [],
        },
        {
            "key": "ccj",
            "label": "County Court Judgements",
            "severity": "OK",
            "description": "No County Court Judgements (CCJs) recorded",
            "details": [],
        },
        {
            "key": "director_linkages",
            "label": "Director linkages",
            "severity": "WARNING",
            "description": "1 historical director linkage to dissolved entity",
            "details": [
                {"director_name": "John Smith", "entity_name": "Legacy Logistics Ltd", "dissolved_on": "2021-09-30"},
            ],
        },
        {
            "key": "bankruptcy",
            "label": "Bankruptcy filings",
            "severity": "OK",
            "description": "No bankruptcy filings",
            "details": [],
        },
    ],
    "company_information": {
        "trading_name": "UrbanNest Home",
        "legal_entity_name": "UrbanNest Retail Group Ltd",
        "company_registration_number": "08934567",
        "industry_code": "47910",
        "industry_description": "Home & Lifestyle",
        "date_of_incorporation": "2018-04-14",
        "vat_number": "GB123456789",
        "contact_number": "+44 121 555 7890",
        "registered_address": "45 Kensington High Street London, W8 5ED, United Kingdom",
        "country": "GB",
        "company_status": "Active",
        "latest_turnover": "500000.00",
        "latest_turnover_currency": "GBP",
    },
    "directors": [
        {
            "name": "John Smith",
            "role": "Director",
            "appointed_on": "2018-03-14",
            "date_of_birth": "1980-06-12",
            "flags": [],
        },
        {
            "name": "Emma Clarke",
            "role": "Director",
            "appointed_on": "2020-09-01",
            "date_of_birth": "1985-02-22",
            "flags": [],
        },
    ],
    "payment_behaviour": (
        "The company generally pays suppliers within agreed terms. Minor delays observed in 2 instances "
        "over the past 12 months, but no severe delinquencies."
    ),
    "last_checked_at": "2026-03-11T09:15:00Z",
    "checked_by_user_id": "user-uuid",
    "created_at": "2026-03-11T09:15:00Z",
    "updated_at": "2026-03-11T09:15:00Z",
}

_APP_DETAIL_EXAMPLE = {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "organization_id": "org-uuid-here",
    "application_number": "APP-2026-00001",
    "state": "ACTIVE",
    "status": "SUBMITTED",
    "company_registration_number": "08934567",
    "vat_registration_number": "GB123456789",
    "industry": "HOME_AND_LIFESTYLE",
    "number_of_employees": "51-200 employees",
    "date_of_incorporation": "2018-04-14",
    "years_trading": 10,
    "annual_turnover": "500000.00",
    "net_profit": "120000.00",
    "trade_references": _TRADE_REFERENCES_EXAMPLE,
    "bank_reference": _BANK_REFERENCE_EXAMPLE,
    "requested_credit_limit": "25000.00",
    "requested_payment_terms_days": 30,
    "expected_monthly_spend": "5000.00",
    "seasonal_peaks": ["January", "December"],
    "justification": "Expected increase in delivery volume during Q4 due to seasonal demand.",
    "director_signatory_name": "David Wilson",
    "director_signatory_position": "Managing Director",
    "declaration_date": "2026-03-25",
    "consent_credit_check": True,
    "consent_terms_and_conditions": True,
    "consent_data_processing": True,
    "submitted_by": {
        "id": "user-uuid-client",
        "first_name": "Natalia",
        "last_name": "James",
    },
    "assigned_reviewer": {
        "id": "user-uuid-reviewer",
        "first_name": "Sarah",
        "last_name": "Mitchell",
    },
    "submitted_at": "2026-03-10T08:00:00Z",
    "reviewer_assigned_at": "2026-03-11T10:00:00Z",
    "references_verified_at": None,
    "decided_at": None,
    "approved_at": None,
    "approved_by": None,
    "rejected_at": None,
    "rejected_by": None,
    "cancelled_at": None,
    "cancelled_by": None,
    "withdrawn_at": None,
    "withdrawn_by": None,
    "approved_credit_limit": None,
    "approved_payment_terms_days": None,
    "review_frequency": None,
    "approval_notes": None,
    "rejection_category": None,
    "rejection_reason": None,
    "cancellation_reason": None,
    "internal_notes": None,
    "deleted_at": None,
    "credit_report": _CREDIT_REPORT_EXAMPLE,
    "created_at": "2026-03-10T08:00:00Z",
    "updated_at": "2026-03-10T08:00:00Z",
}

_APP_CURRENT_DETAIL_EXAMPLE = {**_APP_DETAIL_EXAMPLE, "cooldown": None, "pending_credit_limit_increase_request": None}

_DRAFT_APPLICATION_EXAMPLE = {
    "company_registration_number": "08934567",
    "vat_registration_number": "GB123456789",
    "industry": "HOME_AND_LIFESTYLE",
    "number_of_employees": "51-200 employees",
    "date_of_incorporation": "2018-04-14",
    "years_trading": 10,
    "annual_turnover": "500000.00",
    "net_profit": "120000.00",
    "trade_references": _TRADE_REFERENCES_EXAMPLE,
    "bank_reference": _BANK_REFERENCE_EXAMPLE,
    "requested_credit_limit": "25000.00",
    "requested_payment_terms_days": 30,
    "expected_monthly_spend": "5000.00",
    "seasonal_peaks": ["January", "December"],
    "justification": "Expected increase in delivery volume during Q4 due to seasonal demand.",
    "director_signatory_name": "David Wilson",
    "director_signatory_position": "Managing Director",
    "declaration_date": "2026-03-25",
    "consent_credit_check": True,
    "consent_terms_and_conditions": True,
    "consent_data_processing": True,
}

_APP_CREATED_EXAMPLE = {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "application_number": "APP-2026-00001",
}

_APP_LIST_ITEM_EXAMPLE = {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "application_number": "APP-2026-00421",
    "status": "REJECTED",
    "submitted_at": "2025-01-05T08:00:00Z",
    "requested_credit_limit": "40000.00",
    "assigned_reviewer": {
        "id": "user-uuid",
        "first_name": "Sarah",
        "last_name": "Mitchell",
    },
}

LIST_CREDIT_APPLICATIONS = create_doc_entry(
    summary="List credit applications",
    description="List all credit applications for an organisation. Supports filtering by status and search by application number. Each item is a compact row intended for the applications history table (application number, submission date, requested limit, assigned reviewer, status). When a reviewer is assigned, their id, first name and last name are nested under the `assigned_reviewer` field.",
    responses={
        200: success_entry("Paginated list of credit applications", data={"items": [_APP_LIST_ITEM_EXAMPLE], "total": 1, "page": 1, "size": 20, "pages": 1}),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

_FAILED_DOC_EXAMPLE = {
    "index": 0,
    "filename": "bank-reference-letter.pdf",
    "reason": "Bank reference letter upload failed, please retry.",
}

CREATE_CREDIT_APPLICATION = create_doc_entry(
    summary="Create credit application (direct submit)",
    description=(
        "Create and immediately submit a credit application. Requires all mandatory fields including at least 2 trade references "
        "and all consent declarations. Sent as multipart form-data to support optional bank reference letter file upload. "
        "Returns a compact response with just `id` and `application_number`; fetch the detail endpoint for the full submitted "
        "state. If the bank reference letter fails to upload, the application is still created and the failure is reported "
        "under the top-level `failed_documents` array (with `index`, `filename`, `reason`) so the client can retry just the file."
    ),
    responses={
        201: custom_entry(
            "Credit application submitted",
            example={
                "success": True,
                "message": "Credit application submitted.",
                "data": _APP_CREATED_EXAMPLE,
                "failed_documents": [],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
        422: error_entry("Validation failed", code="validation_error", message="Cannot submit: missing required fields."),
    },
)

GET_CURRENT_CREDIT_APPLICATION_DETAIL = create_doc_entry(
    summary="Get latest credit application detail",
    description=(
        "Same fields as GET credit application by id, plus a `cooldown` object when `status` is `REJECTED` "
        "(`{ active, summary }` from the organisation submission cool-down window; otherwise inactive or null summary). "
        "When `status` is `APPROVED`, `pending_credit_limit_increase_request` is populated if there is a pending "
        "credit limit increase request for the organisation (otherwise null). "
        "Returns the organisation's single most recently updated active application (non-deleted, lifecycle ACTIVE). "
        "404 if no such application exists."
    ),
    responses={
        200: success_entry("Credit application detail with cooldown", data=_APP_CURRENT_DETAIL_EXAMPLE),
        401: error_401_entry(),
        404: error_entry(
            "No application",
            code="not_found",
            message="No credit application found for this organisation.",
        ),
    },
)

GET_CREDIT_APPLICATION_DETAIL = create_doc_entry(
    summary="Get credit application detail",
    description=(
        "Retrieve full detail of a credit application. The response body's `data` is the application object itself "
        "(no extra `application` wrapper). Includes the user-editable fields, trade references, the grouped bank reference "
        "(bank account fields + uploaded reference letter), lifecycle timestamps, decision fields, and the organisation's "
        "Creditsafe credit report joined on — populated once a credit check has run (e.g. while status is "
        "CREDIT_CHECK_COMPLETED or APPROVED) and `null` otherwise. The `submitted_by` and `assigned_reviewer` objects "
        "contain the joined user's id, first name, and last name. When an application is approved, rejected, cancelled, "
        "or withdrawn, the corresponding `approved_at` / `approved_by`, `rejected_at` / `rejected_by`, "
        "`cancelled_at` / `cancelled_by`, or `withdrawn_at` / `withdrawn_by` fields are populated (timestamps plus "
        "the same UserRef shape as `submitted_by`). Does not include submission cool-down; use "
        "GET …/current-application for `cooldown` when viewing the latest application."
    ),
    responses={
        200: success_entry("Credit application detail", data=_APP_DETAIL_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Application not found", code="not_found", message="Credit application not found."),
    },
)

EDIT_COMPANY_FINANCIAL_INFO = create_doc_entry(
    summary="Edit company financial information",
    description=(
        "Update the company financial information section of a submitted credit application. Admin only. "
        "Returns only a success message; fetch the application detail endpoint to see the updated state."
    ),
    responses={
        200: success_entry("Company financial information updated"),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can edit submitted application sections."),
        404: error_entry("Application not found", code="not_found", message="Credit application not found."),
    },
)

EDIT_BANK_REFERENCE = create_doc_entry(
    summary="Edit bank reference",
    description=(
        "Multipart form-data: bank_reference is a JSON object with bank fields, plus an optional "
        "bank_reference_letter_file upload for a replacement letter and an optional deleted_bank_reference_letter_id "
        "form field to remove the current letter. Admin only. At least one of bank_reference updates, "
        "bank_reference_letter_file, or deleted_bank_reference_letter_id must be provided. Returns a success message "
        "and a `failed_documents` array; if the bank reference letter fails to upload, the other field updates still "
        "succeed and the failure is reported under `failed_documents`. Fetch the application detail endpoint to see "
        "the updated state."
    ),
    responses={
        200: custom_entry(
            "Bank reference updated",
            example={
                "success": True,
                "message": "Bank reference updated.",
                "failed_documents": [_FAILED_DOC_EXAMPLE],
            },
        ),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can edit submitted application sections."),
        404: error_entry("Application not found", code="not_found", message="Credit application not found."),
    },
)

EDIT_REQUESTED_CREDIT_TERMS = create_doc_entry(
    summary="Edit requested credit terms",
    description=(
        "Update the requested credit terms section of a submitted credit application. Admin only. "
        "Returns only a success message; fetch the application detail endpoint to see the updated state."
    ),
    responses={
        200: success_entry("Requested credit terms updated"),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can edit submitted application sections."),
        404: error_entry("Application not found", code="not_found", message="Credit application not found."),
    },
)

EDIT_DECLARATIONS = create_doc_entry(
    summary="Edit declarations & consent",
    description=(
        "Update the declarations and consent section of a submitted credit application. Admin only. "
        "Returns only a success message; fetch the application detail endpoint to see the updated state."
    ),
    responses={
        200: success_entry("Declarations updated"),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can edit submitted application sections."),
        404: error_entry("Application not found", code="not_found", message="Credit application not found."),
    },
)

ADD_TRADE_REFERENCE = create_doc_entry(
    summary="Add trade reference",
    description=(
        "Add an additional trade reference to a submitted credit application. Maximum 5 trade references allowed. "
        "Admin only. Returns only a success message; fetch the application detail endpoint to see the updated state."
    ),
    responses={
        201: success_entry("Trade reference added"),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can add trade references."),
        404: error_entry("Application not found", code="not_found", message="Credit application not found."),
        422: error_entry("Maximum reached", code="validation_error", message="Maximum 5 trade references allowed."),
    },
)

EDIT_TRADE_REFERENCE = create_doc_entry(
    summary="Edit trade reference",
    description=(
        "Update details of a specific trade reference on a credit application. Admin only. "
        "Returns only a success message; fetch the application detail endpoint to see the updated state."
    ),
    responses={
        200: success_entry("Trade reference updated"),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can update trade references."),
        404: error_entry("Trade reference not found", code="not_found", message="Trade reference not found."),
    },
)

VERIFY_TRADE_REFERENCE = create_doc_entry(
    summary="Update trade reference verification status",
    description=(
        "Set the verification status of a trade reference (PENDING, VERIFIED, DECLINED, UNABLE_TO_VERIFY). When all "
        "references are verified, references_verified_at is set. Admin only. Returns only a success message; fetch the "
        "application detail endpoint to see the updated state."
    ),
    responses={
        200: success_entry("Trade reference verification updated"),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can update trade reference verification."),
        404: error_entry("Trade reference not found", code="not_found", message="Trade reference not found."),
        422: error_entry("Invalid state", code="validation_error", message="Trade references can only be verified during review."),
    },
)

ASSIGN_CREDIT_REVIEWER = create_doc_entry(
    summary="Assign reviewer",
    description=(
        "Assign or reassign a reviewer to a credit application. Moves the application to REVIEWER_ASSIGNED status. "
        "Admin only. Returns only a success message; fetch the application detail endpoint to see the updated state."
    ),
    responses={
        200: success_entry("Reviewer assigned"),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can assign reviewers."),
        404: error_entry("Application not found", code="not_found", message="Credit application not found."),
    },
)

_CREDIT_INVESTIGATION_EXAMPLE = {
    "id": "investigation-uuid",
    "status": "IN_PROGRESS",
    "provider_reference": "CS-FRESH-2026-00042",
    "connect_id": None,
    "reg_no": "08934567",
    "company_name": "Example Trading Ltd",
    "country": "GB",
    "requested_at": "2026-03-11T09:15:00Z",
    "completed_at": None,
    "failure_reason": None,
}

_CREDIT_CHECK_COMPLETED_EXAMPLE = {
    "outcome": "COMPLETED",
    "report": _CREDIT_REPORT_EXAMPLE,
    "investigation": None,
    "message": "Credit check completed successfully.",
}

_CREDIT_CHECK_INVESTIGATION_EXAMPLE = {
    "outcome": "INVESTIGATION_PROGRESS",
    "report": None,
    "investigation": _CREDIT_INVESTIGATION_EXAMPLE,
    "message": "No credit report available yet. A fresh investigation has been ordered and typically takes 2-3 business days.",
}

_CREDIT_CHECK_FAILED_EXAMPLE = {
    "outcome": "FAILED",
    "report": None,
    "investigation": None,
    "message": "Credit check failed. Please try again later.",
}

RUN_CREDIT_CHECK = create_doc_entry(
    summary="Run credit assessment",
    description=(
        "Trigger a Creditsafe credit assessment for the application. Admin only. "
        "There are three possible outcomes, signalled by the `outcome` field in the response: "
        "`COMPLETED` when Creditsafe returned a report (stored on the organisation and returned here), "
        "`INVESTIGATION_PROGRESS` when no matching company was found and a fresh investigation has been "
        "ordered (takes 2-3 business days; poll the refresh endpoint later), and `FAILED` on any other "
        "error. The application's status is transitioned accordingly "
        "(`CREDIT_CHECK_COMPLETED`, `CREDIT_CHECK_INVESTIGATION_PROGRESS`, `CREDIT_CHECK_FAILED`)."
    ),
    responses={
        200: custom_entry(
            "Credit check processed",
            example={
                "success": True,
                "message": "Credit check completed successfully.",
                "data": _CREDIT_CHECK_COMPLETED_EXAMPLE,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can run the credit check."),
        422: error_entry("Invalid state", code="validation_error", message="Credit check can only run while the application is under review."),
    },
)

REFRESH_CREDIT_CHECK = create_doc_entry(
    summary="Refresh credit check after fresh investigation",
    description=(
        "Re-query Creditsafe for a credit report on an application whose previous run "
        "opened a fresh investigation (status `CREDIT_CHECK_INVESTIGATION_PROGRESS`). Admin only. "
        "Because fresh investigations take 2-3 business days, the frontend calls this endpoint "
        "on demand to check if results are available. When Creditsafe now returns a report, "
        "it is stored on the organisation, the investigation is marked `COMPLETED`, and the "
        "application is moved to `CREDIT_CHECK_COMPLETED`. If the investigation is still in "
        "progress, the existing investigation record is returned unchanged. On any unexpected "
        "error the investigation is marked `FAILED` and the application is moved to "
        "`CREDIT_CHECK_FAILED`."
    ),
    responses={
        200: custom_entry(
            "Credit check refresh processed",
            example={
                "success": True,
                "message": "Credit report retrieved after fresh investigation completed.",
                "data": _CREDIT_CHECK_COMPLETED_EXAMPLE,
            },
        ),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can refresh the credit check."),
        404: error_entry("Investigation not found", code="not_found", message="No investigation is in progress for this application."),
        422: error_entry("Invalid state", code="validation_error", message="Credit check refresh is only available while an investigation is in progress."),
    },
)

READY_FOR_DECISION = create_doc_entry(
    summary="Mark ready for decision",
    description=(
        "Move the application to READY_FOR_DECISION status. Requires all trade references verified and credit "
        "assessment completed. Admin only. Returns only a success message; fetch the application detail endpoint to "
        "see the updated state."
    ),
    responses={
        200: success_entry("Application ready for decision"),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can move an application to decision pending."),
        422: error_entry("Pre-conditions not met", code="validation_error", message="All trade references must be verified first."),
    },
)

APPROVE_CREDIT_APPLICATION = create_doc_entry(
    summary="Approve credit application",
    description=(
        "Approve the credit application with credit limit, payment terms, and review frequency. Admin only. "
        "Returns only a success message; fetch the application detail endpoint to see the updated state."
    ),
    responses={
        200: success_entry("Credit application approved"),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can approve credit applications."),
        422: error_entry("Invalid state", code="validation_error", message="Application is not in a state that allows approval."),
    },
)

REJECT_CREDIT_APPLICATION = create_doc_entry(
    summary="Reject credit application",
    description=(
        "Reject the credit application with a rejection category and detailed reason. Admin only. "
        "Starts the organisation submission cool-down window (policy-driven duration) so re-application is "
        "blocked until it expires. Returns only a success message; fetch the application detail endpoint to see "
        "the updated state and `cooldown` summary."
    ),
    responses={
        200: success_entry("Credit application rejected"),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can reject credit applications."),
        422: error_entry("Invalid state", code="validation_error", message="Application is not in a state that allows rejection."),
    },
)

CANCEL_CREDIT_APPLICATION = create_doc_entry(
    summary="Cancel credit application",
    description=(
        "Cancel a credit application with a reason. Admin only. Returns only a success message; fetch the "
        "application detail endpoint to see the updated state."
    ),
    responses={
        200: success_entry("Credit application cancelled"),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only admins can cancel credit applications."),
    },
)

WITHDRAW_CREDIT_APPLICATION = create_doc_entry(
    summary="Withdraw credit application",
    description=(
        "Client withdraws their own application. Only allowed for draft or submitted applications. "
        "Returns only a success message; fetch the application detail endpoint to see the updated state."
    ),
    responses={
        200: success_entry("Credit application withdrawn"),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Use cancel instead of withdraw for admin users."),
        422: error_entry("Invalid state", code="validation_error", message="Withdraw is only allowed for draft or submitted applications."),
    },
)

DELETE_CREDIT_APPLICATION = create_doc_entry(
    summary="Delete credit application",
    description="Soft-delete a credit application. Organisation users can only delete draft or withdrawn applications.",
    responses={
        200: success_entry("Credit application deleted", data=None),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Organisation users can only delete draft or withdrawn applications."),
        404: error_entry("Application not found", code="not_found", message="Credit application not found."),
    },
)

_DRAFT_DATA_EXAMPLE = {
    "id": "draft-uuid",
    "draft_number": "CAD-001",
    "created_at": "2026-02-12T09:32:00Z",
}

_DRAFT_DETAIL_EXAMPLE = {
    **_DRAFT_DATA_EXAMPLE,
    "application": _DRAFT_APPLICATION_EXAMPLE,
}

SAVE_CREDIT_APPLICATION_DRAFT = create_doc_entry(
    summary="Create credit application draft",
    description=(
        "Create a new draft credit application. All fields are optional — save any wizard step progress. Sent as multipart "
        "form-data to support optional file uploads. If the bank reference letter fails to upload, the draft is still created "
        "and the failure is reported under the top-level `failed_documents` array so the client can retry just the file."
    ),
    responses={
        201: custom_entry(
            "Credit application draft saved",
            example={
                "success": True,
                "message": "Credit application draft saved.",
                "data": _DRAFT_DATA_EXAMPLE,
                "failed_documents": [],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

LIST_CREDIT_APPLICATION_DRAFTS = create_doc_entry(
    summary="List credit application drafts",
    description="List all open draft credit applications for an organisation. Each item is a compact row: the draft id, when it was created, the actor that created it (ADMIN when the creator is an admin or super admin, CLIENT when the creator is a B2B customer), and the creator's id and email nested under `created_by`.",
    responses={
        200: success_entry(
            "Paginated list of drafts",
            data={
                "items": [
                    {
                        "draft_id": "draft-uuid",
                        "created_at": "2026-02-12T09:32:00Z",
                        "actor": "ADMIN",
                        "created_by": {
                            "id": "user-uuid",
                            "email": "natalia.james@swcouriers.co.uk",
                        },
                    },
                ],
                "total": 1,
                "page": 1,
                "size": 20,
                "pages": 1,
            },
        ),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

GET_CREDIT_APPLICATION_DRAFT = create_doc_entry(
    summary="Get credit application draft",
    description=(
        "Retrieve a draft credit application with its stored input fields, trade references, and the grouped bank reference "
        "(bank account fields plus the optional uploaded reference letter). Only returns the user-editable fields — lifecycle, "
        "decision, and review fields are not included."
    ),
    responses={
        200: success_entry("Draft detail", data=_DRAFT_DETAIL_EXAMPLE),
        401: error_401_entry(),
        404: error_entry("Draft not found", code="not_found", message="Credit application draft not found."),
    },
)

PATCH_CREDIT_APPLICATION_DRAFT = create_doc_entry(
    summary="Update credit application draft",
    description=(
        "Update an existing draft credit application. Supports partial updates and multipart form-data bank reference letter "
        "replacement via bank_reference_letter or deletion via deleted_bank_reference_letter_id. If the bank reference letter "
        "fails to upload, the rest of the update still succeeds and the failure is reported under the top-level "
        "`failed_documents` array."
    ),
    responses={
        200: custom_entry(
            "Credit application draft updated",
            example={
                "success": True,
                "message": "Credit application draft updated.",
                "data": _DRAFT_DATA_EXAMPLE,
                "failed_documents": [],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Draft not found", code="not_found", message="Credit application draft not found."),
        422: error_entry("Already published", code="validation_error", message="This draft has already been published."),
    },
)

PUBLISH_CREDIT_APPLICATION_DRAFT = create_doc_entry(
    summary="Publish (submit) credit application draft",
    description=(
        "Submit a draft credit application. Accepts the same `application_data` body as the direct-create endpoint (multipart "
        "form-data, JSON-encoded). Any fields provided are merged onto the draft's stored application before submission, and "
        "`trade_references` (when sent) fully replaces the draft's existing references; omit it to keep what's already on the "
        "draft. The merged record is then validated against the submission requirements (required fields, bank details, at "
        "least two trade references, and all consents). Supports optional bank reference letter replacement via "
        "bank_reference_letter_file and optional deletion via deleted_bank_reference_letter_id before publish. Returns the "
        "compact draft reference (id, draft_number, created_at); fetch the draft detail endpoint to see the published "
        "application data. If the bank reference letter fails to upload, the application is still submitted and the failure is "
        "reported under the top-level `failed_documents` array."
    ),
    responses={
        200: custom_entry(
            "Credit application submitted",
            example={
                "success": True,
                "message": "Credit application submitted.",
                "data": _DRAFT_DATA_EXAMPLE,
                "failed_documents": [],
            },
        ),
        401: error_401_entry(),
        404: error_entry("Draft not found", code="not_found", message="Credit application draft not found."),
        422: error_entry("Validation failed", code="validation_error", message="Cannot submit application: missing required fields."),
    },
)

DELETE_CREDIT_APPLICATION_DRAFT = create_doc_entry(
    summary="Delete credit application draft",
    description="Delete a draft credit application and its associated data. The underlying application is soft-deleted.",
    responses={
        200: success_entry("Draft deleted", data=None),
        401: error_401_entry(),
        404: error_entry("Draft not found", code="not_found", message="Credit application draft not found."),
        422: error_entry("Invalid state", code="validation_error", message="Cannot delete a draft that is no longer in draft status."),
    },
)

CREATE_CREDIT_LIMIT_INCREASE_REQUEST = create_doc_entry(
    summary="Request a credit limit increase",
    description=(
        "Organisation users with profile write access submit a new limit increase request. "
        "The current operational limit is snapshotted as `previous_limit`. Only one pending request is allowed per organisation."
    ),
    responses={
        201: success_entry("Request created", data={"id": "uuid", "status": "PENDING"}),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
        422: error_entry(
            "Validation failed",
            code="validation_error",
            message="A pending request already exists or no credit account is present.",
        ),
    },
)

LIST_CREDIT_LIMIT_INCREASE_REQUESTS = create_doc_entry(
    summary="List credit limit increase requests",
    description="Paginated history of limit increase requests for the organisation (newest first).",
    responses={
        200: success_entry("Requests", data={"items": [], "total": 0, "page": 1, "size": 20, "pages": 0}),
        401: error_401_entry(),
        404: error_entry("Organisation not found", code="not_found", message="Organisation not found."),
    },
)

GET_CREDIT_LIMIT_INCREASE_REQUEST = create_doc_entry(
    summary="Get a credit limit increase request by id",
    description="Returns request details including `requested_by` and `reviewed_by` as user summaries.",
    responses={
        200: success_entry("Request", data={}),
        401: error_401_entry(),
        404: error_entry("Not found", code="not_found", message="Request not found."),
    },
)

APPROVE_CREDIT_LIMIT_INCREASE_REQUEST = create_doc_entry(
    summary="Approve a pending credit limit increase request",
    description=(
        "Admin-only. Applies the approved limit to the organisation credit account (effective immediately when the "
        "effective date is today) and appends a row to credit limit adjustment history with justification taken from "
        "the request's reason text. Updates the request to APPROVED with `approved_limit` set to the value provided "
        "(which may differ from the originally requested amount)."
    ),
    responses={
        200: success_entry("Request approved", data={}),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only administrators can approve this request."),
        404: error_entry("Not found", code="not_found", message="Request not found."),
        422: error_entry("Invalid state", code="validation_error", message="Only a pending request can be approved."),
    },
)

REJECT_CREDIT_LIMIT_INCREASE_REQUEST = create_doc_entry(
    summary="Reject a pending credit limit increase request",
    description="Admin-only. Marks the request as REJECTED without changing the credit account limit.",
    responses={
        200: success_entry("Request rejected", data={}),
        401: error_401_entry(),
        403: error_entry("Forbidden", code="forbidden", message="Only administrators can reject this request."),
        404: error_entry("Not found", code="not_found", message="Request not found."),
        422: error_entry("Invalid state", code="validation_error", message="Only a pending request can be rejected."),
    },
)
