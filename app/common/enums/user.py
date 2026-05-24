import enum


class UserTitle(enum.StrEnum):
    MR = "MR"
    MRS = "MRS"
    MS = "MS"
    DR = "DR"
    PROF = "PROF"


class UserRole(enum.StrEnum):
    SUPER_ADMIN = "SUPER_ADMIN"
    ADMIN = "ADMIN"
    WAREHOUSE_STAFF = "WAREHOUSE_STAFF"
    DRIVER = "DRIVER"
    CUSTOMER_B2B = "CUSTOMER_B2B"
    CUSTOMER_B2C = "CUSTOMER_B2C"


class UserStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
    SUSPENDED = "SUSPENDED"
    PENDING_VERIFICATION = "PENDING_VERIFICATION"


class UserInactiveReason(enum.StrEnum):
    """Why a user account is INACTIVE (null when not applicable)."""

    INACTIVITY = "INACTIVITY"


class ClientType(enum.StrEnum):
    """X-Client-Type header: which app/portal the request is from."""

    ADMIN = "ADMIN"
    CUSTOMER_B2B = "CUSTOMER_B2B"
    CUSTOMER_B2C = "CUSTOMER_B2C"
    WAREHOUSE = "WAREHOUSE"
    DRIVER = "DRIVER"


ROLE_TO_CLIENT_TYPE: dict[str, ClientType] = {
    UserRole.SUPER_ADMIN: ClientType.ADMIN,
    UserRole.ADMIN: ClientType.ADMIN,
    UserRole.WAREHOUSE_STAFF: ClientType.WAREHOUSE,
    UserRole.DRIVER: ClientType.DRIVER,
    UserRole.CUSTOMER_B2B: ClientType.CUSTOMER_B2B,
    UserRole.CUSTOMER_B2C: ClientType.CUSTOMER_B2C,
}
