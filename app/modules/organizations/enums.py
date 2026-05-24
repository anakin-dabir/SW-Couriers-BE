"""Organization enums."""

import enum


class OrganizationStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ON_HOLD = "ON_HOLD"
    SUSPENDED = "SUSPENDED"
    INACTIVE = "INACTIVE"


class IndustryType(enum.StrEnum):
    ECOMMERCE = "ECOMMERCE"  # E-commerce
    HOME_LIFESTYLE = "HOME_LIFESTYLE"  # Home & Lifestyle
    RETAIL = "RETAIL"  # Retail
    WHOLESALE_DISTRIBUTION = "WHOLESALE_DISTRIBUTION"  # Wholesale & Distribution
    HEALTHCARE_PHARMA = "HEALTHCARE_PHARMA"  # Healthcare & Pharmaceuticals
    TECHNOLOGY_SOFTWARE = "TECHNOLOGY_SOFTWARE"  # Technology & Software
    MANUFACTURING = "MANUFACTURING"
    LOGISTICS_TRANSPORT = "LOGISTICS_TRANSPORT"  # Logistics & Transport
    CONSTRUCTION = "CONSTRUCTION"
    FOOD_BEVERAGE = "FOOD_BEVERAGE"  # Food & Beverage
    FINANCE_INSURANCE = "FINANCE_INSURANCE"  # Finance & Insurance
    EDUCATION = "EDUCATION"
    MEDIA_ENTERTAINMENT = "MEDIA_ENTERTAINMENT"  # Media & Entertainment
    OTHER = "OTHER"


class CompanySize(enum.StrEnum):
    EMPLOYEES_1_10 = "1-10 employees"  # 1–10
    EMPLOYEES_11_50 = "11-50 employees"  # 11–50
    EMPLOYEES_51_200 = "51-200 employees"  # 51–200
    EMPLOYEES_201_500 = "201-500 employees"  # 201–500
    EMPLOYEES_501_1000 = "501-1000 employees"  # 501–1000
    EMPLOYEES_1000_PLUS = "1000+ employees"  # 1000+


class ContactRole(enum.StrEnum):
    ACCOUNT_OWNER = "ACCOUNT_OWNER"  # primary owner; at least one required per org
    BILLING = "BILLING"
    OPERATIONS = "OPERATIONS"
    TECHNICAL = "TECHNICAL"
    OTHER = "OTHER"


class ContactStatus(enum.StrEnum):
    PENDING = "PENDING"  # invite not yet accepted
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class PaymentModel(enum.StrEnum):
    CARD = "CARD"
    BANK_TRANSFER = "BANK_TRANSFER"
    CREDIT_ACCOUNT = "CREDIT_ACCOUNT"
    CASH = "CASH"


class BillingSchedule(enum.StrEnum):
    IMMEDIATE = "IMMEDIATE"
    FIXED_MONTHLY_DATE = "FIXED_MONTHLY_DATE"
    DAYS_AFTER_ORDER = "DAYS_AFTER_ORDER"


class VatRate(enum.StrEnum):
    STANDARD_20 = "STANDARD_20"
    REDUCED_5 = "REDUCED_5"
    ZERO_RATED = "ZERO_RATED"
    EXEMPT = "EXEMPT"


class VatTreatment(enum.StrEnum):
    UK = "UK"
    OVERSEAS = "OVERSEAS"


class OrgDocumentStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    EXPIRING_SOON = "EXPIRING_SOON"  # within 30 days of expiry_date


class OrgDocumentCategory(enum.StrEnum):
    CONTRACTS = "CONTRACTS"
    INTERNAL = "INTERNAL"
    CLIENT_UPLOADS = "CLIENT_UPLOADS"


class OrgDocumentType(enum.StrEnum):
    MSA = "MSA"                                          # Master Service Agreement
    SLA = "SLA"                                          # Service Level Agreement
    PRICING = "PRICING"                                  # Pricing Schedule
    NDA = "NDA"                                          # Non-Disclosure Agreement
    DPA = "DPA"                                          # Data Processing Agreement
    COMPANY_REGISTRATION_CERT = "COMPANY_REGISTRATION_CERT"
    VAT_REGISTRATION_CERT = "VAT_REGISTRATION_CERT"
    PUBLIC_LIABILITY_INSURANCE = "PUBLIC_LIABILITY_INSURANCE"
    EMPLOYERS_LIABILITY_INSURANCE = "EMPLOYERS_LIABILITY_INSURANCE"
    GOODS_IN_TRANSIT_INSURANCE = "GOODS_IN_TRANSIT_INSURANCE"
    BANK_REFERENCE_LETTER = "BANK_REFERENCE_LETTER"
    TRADE_REFERENCE_LETTER = "TRADE_REFERENCE_LETTER"
    PROOF_OF_ADDRESS = "PROOF_OF_ADDRESS"
    DIRECTOR_ID_VERIFICATION = "DIRECTOR_ID_VERIFICATION"
    FINANCIAL_STATEMENTS = "FINANCIAL_STATEMENTS"
    CREDIT_TERMS_CONDITIONS = "CREDIT_TERMS_CONDITIONS"
    LETTER_OF_AUTHORITY = "LETTER_OF_AUTHORITY"
    OTHER = "OTHER"


class OrgDocumentConfidentialityLevel(enum.StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    STRICTLY_CONFIDENTIAL = "STRICTLY_CONFIDENTIAL"


class OrgDocumentActivityType(enum.StrEnum):
    UPLOADED = "UPLOADED"
    DOWNLOADED = "DOWNLOADED"
    VIEWED = "VIEWED"
    SHARED = "SHARED"
    EXPIRED = "EXPIRED"
    DELETED = "DELETED"
    REVOKED = "REVOKED"
    EXTENDED = "EXTENDED"


class OrgDocumentShareStatus(enum.StrEnum):
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    DOWNLOADED = "DOWNLOADED"
