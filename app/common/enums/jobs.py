import enum


# Values must match worker function __name__ exactly (Arq looks up by this string).
class Job(enum.StrEnum):
    SEND_INVITE_EMAIL = "send_invite_email_task"
    SEND_PASSWORD_RESET_EMAIL = "send_password_reset_email_task"
    SEND_SUPPORT_ISSUED_PASSWORD_EMAIL = "send_support_issued_password_email_task"
    SEND_VERIFICATION_EMAIL = "send_verification_email_task"

    RUN_DAILY_SUSPENSION_RULES = "run_daily_suspension_rules_task"
    EVALUATE_STATUS_AUTOMATION_RULES = "evaluate_status_automation_rules_task"
    RUN_DAILY_STATUS_AUTOMATION_RECONCILIATION = "run_daily_status_automation_reconciliation_task"

    SEND_DRIVER_ACTIVATION_EMAIL = "send_driver_activation_email_task"

    GENERATE_INVOICE_PDF = "generate_invoice_pdf_task"
    GENERATE_ACCOUNT_STATEMENT_PDF = "generate_account_statement_pdf_task"
    DELIVER_SCHEDULED_ACCOUNT_STATEMENT = "deliver_scheduled_account_statement_task"
    RUN_ACCOUNT_STATEMENT_SCHEDULES = "run_account_statement_schedules_task"

    PROCESS_NOTIFICATION = "process_notification_task"

    SEND_DOCUMENT_SHARE_EMAIL = "send_document_share_email_task"

    SEND_DOC_OTP_EMAIL = "send_doc_otp_email_task"
    SEND_SHARE_OTP_EMAIL = "send_share_otp_email_task"

    EVALUATE_ORG_CREDIT_ALERTS = "evaluate_org_credit_alerts_task"
    AUTO_UNSNOOZE_CREDIT_ALERTS = "auto_unsnooze_credit_alerts_task"
    SEND_CREDIT_ALERT_EMAIL = "send_credit_alert_email_task"
    SYNC_QB_CUSTOMER = "sync_qb_customer_task"
    SYNC_QB_INVOICE = "sync_qb_invoice_task"
    SYNC_QB_CREDIT_NOTE = "sync_qb_credit_note_task"
    SYNC_QB_PAYMENT = "sync_qb_payment_task"
    VOID_QB_CREDIT_NOTE = "void_qb_credit_note_task"
    VOID_QB_CREDIT_NOTE_CHAIN = "void_qb_credit_note_chain_task"
