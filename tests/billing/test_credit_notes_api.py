"""Legacy smoke imports — detailed coverage lives in test_credit_notes_b2b_api and test_credit_notes_admin_api."""

from tests.billing.test_credit_notes_b2b_api import (  # noqa: F401
    test_b2b_apply_credit_note_full_amount,
    test_b2b_list_returns_all_org_credit_notes_by_default,
)
from tests.billing.test_credit_notes_admin_api import (  # noqa: F401
    test_admin_create_credit_note_with_customer,
    test_admin_send_credit_note_to_client,
)
