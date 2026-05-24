"""Enums for service tier configuration."""

import enum


class ServiceTierAudience(enum.StrEnum):
    """Who this service tier is available for."""

    CUSTOMER_B2B = "CUSTOMER_B2B"
    CUSTOMER_B2C = "CUSTOMER_B2C"
    BOTH = "BOTH"


class ServiceTierStatus(enum.StrEnum):
    """Lifecycle status of a service tier."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class ServiceTierScopeType(enum.StrEnum):
    """Whether the tier is the global default or an organisation-specific row."""

    GLOBAL = "GLOBAL"
    ORG = "ORG"
