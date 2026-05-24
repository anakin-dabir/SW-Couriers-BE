"""Model registry — import ALL ORM models to ensure SQLAlchemy mappers resolve."""

# Base + mixins
import app.common.models  # noqa: F401
import app.integrations.quickbooks.models  # noqa: F401

# All domain models (alphabetical order)
import app.modules.account_statements.models  # noqa: F401
import app.modules.addresses.models  # noqa: F401
import app.modules.admins.models  # noqa: F401
import app.modules.audit.models  # noqa: F401
import app.modules.auth.models  # noqa: F401
import app.modules.billing.models  # noqa: F401
import app.modules.client_inactivity.models  # noqa: F401
import app.modules.crew.models  # noqa: F401
import app.modules.customers.models  # noqa: F401
import app.modules.depots.models  # noqa: F401
import app.modules.drivers.models  # noqa: F401
import app.modules.dropdown_configs.models  # noqa: F401
import app.modules.holidays.models  # noqa: F401
import app.modules.invoices.models  # noqa: F401
import app.modules.notifications.models  # noqa: F401
import app.modules.orders.models  # noqa: F401  # noqa: F401
import app.modules.org_credit.models  # noqa: F401
import app.modules.org_credit_alerts.models  # noqa: F401
import app.modules.org_credit_applications.models  # noqa: F401
import app.modules.org_credit_reviews.models  # noqa: F401
import app.modules.org_credit_settings.models  # noqa: F401
import app.modules.org_credit_suspension.models  # noqa: F401
import app.modules.org_discounts.models  # noqa: F401
import app.modules.org_notes.models  # noqa: F401
import app.modules.organizations.models  # noqa: F401
import app.modules.payments.models  # noqa: F401
import app.modules.permission.models  # noqa: F401
import app.modules.pickup_addresses.models  # noqa: F401
import app.modules.planning.models  # noqa: F401
import app.modules.regions.models  # noqa: F401
import app.modules.service_tiers.models  # noqa: F401
import app.modules.shipments.models  # noqa: F401
import app.modules.status_automation_rules.models  # noqa: F401
import app.modules.suspension_rules.models  # noqa: F401
import app.modules.team_availability.models  # noqa: F401
import app.modules.tracking.models  # noqa: F401
import app.modules.user.models  # noqa: F401
import app.modules.vehicle_inspections.models  # noqa: F401
import app.modules.vehicles.models  # noqa: F401
import app.modules.warehouse.models  # noqa: F401
import app.modules.webhooks.models  # noqa: F401
