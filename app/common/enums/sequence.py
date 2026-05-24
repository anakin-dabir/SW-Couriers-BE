import enum


class SequentialPrefix(enum.StrEnum):
    ADMIN = "ADM"
    VEHICLE = "VAN"
    ROUTE = "RTE"
    MAINTENANCE = "MT"
    DEFECT = "DF"
    DRAFT = "DR"
    ORDER = "SWC-ORD"
    MASTER_LABEL = "ML"
    STOP_TRACKING = "TRK"
    PACKAGE_REFERENCE = "PKG"
    CREDIT_APP_DRAFT = "CAD"
    CREDIT_APP = "APP"
