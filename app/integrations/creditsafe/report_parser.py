"""Parse a raw Creditsafe credit report payload into the canonical
column shape used by ``OrgCreditReport``.

The parser is intentionally tolerant — Creditsafe reports can omit
entire sections for thin-file companies, so every lookup is defensive
and returns ``None``/empty list rather than raising.

The canonical shape produced here mirrors the sections of the credit
report UI (Score Calculated, Risk Indicators, Company Information,
Directors, Payment Behaviour) so that downstream code can read each
value directly off the ORM model without re-parsing JSON.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

RiskSeverity = Literal["OK", "WARNING", "ALERT"]


def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        v = value.strip()
        return v or None
    return str(value)


def _iso_date(value: Any) -> str | None:
    """Return an ISO date string (YYYY-MM-DD) if ``value`` looks like a date."""
    if not value:
        return None
    if isinstance(value, str):
        return value[:10] if len(value) >= 10 else None
    return None


def _format_address(main: dict[str, Any]) -> str | None:
    if not main:
        return None
    simple = main.get("simpleValue")
    if simple:
        return simple
    parts = [
        main.get("street"),
        main.get("houseNumber"),
        main.get("city"),
        main.get("province"),
        main.get("postCode"),
        main.get("country"),
    ]
    joined = ", ".join([p for p in parts if p])
    return joined or None


def _director_name(entry: dict[str, Any]) -> str | None:
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    if name:
        return name
    first = entry.get("firstName") or entry.get("firstNames") or ""
    last = entry.get("lastName") or entry.get("surname") or ""
    full = f"{first} {last}".strip()
    return full or None


def build_directors(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the "Directors Information" panel rows.

    Matches each director against Creditsafe's negative director entries
    (``negativeInformation.negativeDirectorships`` and per-director
    ``additionalData.negativeIndicators``) and attaches human-readable
    strings in ``flags``. Entries without a name are skipped.
    """
    directors_section = raw.get("directors") or {}
    if isinstance(directors_section, list):
        current = directors_section
    else:
        current = directors_section.get("currentDirectors") or []

    negative_section = raw.get("negativeInformation") or {}
    negative_directorships = negative_section.get("negativeDirectorships") or []

    flags_by_name: dict[str, list[str]] = {}
    for nd in negative_directorships:
        if not isinstance(nd, dict):
            continue
        name = _director_name(nd)
        if not name:
            continue
        description = (
            nd.get("description")
            or nd.get("reason")
            or nd.get("type")
            or "Historical negative directorship"
        )
        flags_by_name.setdefault(name.lower(), []).append(description)

    results: list[dict[str, Any]] = []
    for entry in current:
        if not isinstance(entry, dict):
            continue
        name = _director_name(entry)
        if not name:
            continue
        role = entry.get("directorType") or entry.get("position") or entry.get("role")
        appointed = (
            entry.get("appointedDate")
            or entry.get("dateOfAppointment")
            or entry.get("appointmentDate")
        )
        dob = entry.get("dateOfBirth") or entry.get("birthDate")

        direct_flags: list[str] = []
        extra = entry.get("additionalData") or {}
        for flag in extra.get("negativeIndicators") or []:
            if isinstance(flag, dict):
                desc = flag.get("description") or flag.get("type")
                if desc:
                    direct_flags.append(desc)
            elif isinstance(flag, str):
                direct_flags.append(flag)

        mapped_flags = flags_by_name.get(name.lower(), [])
        flags = [*direct_flags, *mapped_flags]

        results.append({
            "name": name,
            "role": role,
            "appointed_on": _iso_date(appointed),
            "date_of_birth": _iso_date(dob),
            "flags": flags,
        })

    return results


def _indicator(
    key: str,
    label: str,
    *,
    count: int,
    singular: str,
    plural: str,
    empty: str,
    details: list[dict[str, Any]],
    warning_severity: RiskSeverity = "WARNING",
    alert_threshold: int = 3,
) -> dict[str, Any]:
    if count <= 0:
        severity: RiskSeverity = "OK"
        description = empty
    else:
        severity = "ALERT" if count >= alert_threshold else warning_severity
        description = f"{count} {singular if count == 1 else plural}"
    return {
        "key": key,
        "label": label,
        "severity": severity,
        "description": description,
        "details": details,
    }


def build_risk_indicators(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the "Risk Indicators" panel rows.

    Produces a fixed list of canonical indicators (insolvency, ccj,
    bankruptcy, director_linkages) so the UI can render a stable set of
    rows. Each entry always has a human-readable ``description`` and a
    ``details`` list populated when issues are present.
    """
    negative = raw.get("negativeInformation") or {}

    insolvencies = negative.get("insolvencies") or negative.get("insolvencySummary") or []
    if isinstance(insolvencies, dict):
        insolvency_count = _to_int(insolvencies.get("activeCount")) or 0
        insolvency_details: list[dict[str, Any]] = []
    else:
        insolvency_details = [x for x in insolvencies if isinstance(x, dict)]
        insolvency_count = len(insolvency_details)

    ccj_list = negative.get("ccjsList") or negative.get("countyCourtJudgements") or []
    ccj_summary = negative.get("ccjSummary") or {}
    if ccj_list and isinstance(ccj_list, list):
        ccj_details = [x for x in ccj_list if isinstance(x, dict)]
        ccj_count = len(ccj_details)
    else:
        ccj_count = _to_int(ccj_summary.get("activeCcjCount")) or 0
        ccj_details = []

    bankruptcies = negative.get("bankruptcies") or []
    if isinstance(bankruptcies, list):
        bankruptcy_details = [x for x in bankruptcies if isinstance(x, dict)]
        bankruptcy_count = len(bankruptcy_details)
    else:
        bankruptcy_details = []
        bankruptcy_count = _to_int((negative.get("bankruptcySummary") or {}).get("count")) or 0

    director_linkages = negative.get("negativeDirectorships") or []
    director_details = [x for x in director_linkages if isinstance(x, dict)]
    director_count = len(director_details)

    return [
        _indicator(
            "insolvency",
            "Insolvency",
            count=insolvency_count,
            singular="active insolvency proceeding",
            plural="active insolvency proceedings",
            empty="No active insolvency proceedings",
            details=insolvency_details,
        ),
        _indicator(
            "ccj",
            "County Court Judgements",
            count=ccj_count,
            singular="County Court Judgement (CCJ) recorded",
            plural="County Court Judgements (CCJs) recorded",
            empty="No County Court Judgements (CCJs) recorded",
            details=ccj_details,
        ),
        _indicator(
            "director_linkages",
            "Director linkages",
            count=director_count,
            singular="historical director linkage to dissolved entity",
            plural="historical director linkages to dissolved entities",
            empty="No adverse director linkages",
            details=director_details,
        ),
        _indicator(
            "bankruptcy",
            "Bankruptcy filings",
            count=bankruptcy_count,
            singular="bankruptcy filing",
            plural="bankruptcy filings",
            empty="No bankruptcy filings",
            details=bankruptcy_details,
        ),
    ]


def _payment_behaviour_description(raw: dict[str, Any]) -> str | None:
    payment = raw.get("paymentData") or raw.get("paymentBehaviour") or {}
    if isinstance(payment, str):
        return payment
    if not isinstance(payment, dict):
        return None
    for key in ("description", "commentary", "summary", "narrative"):
        val = payment.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    dbt = payment.get("dbt") or payment.get("daysBeyondTerms")
    trend = payment.get("trend")
    if dbt is not None and trend:
        return f"Average {dbt} days beyond terms ({trend} trend)."
    return None


def _previous_rating(raw: dict[str, Any]) -> tuple[str | None, str | None]:
    credit_score = raw.get("creditScore") or {}
    previous = credit_score.get("previousCreditRating") or credit_score.get("previousRating") or {}
    rating = previous.get("commonValue") or previous.get("value")
    changed = (
        previous.get("changeDate")
        or previous.get("date")
        or previous.get("effectiveDate")
    )
    return _to_str(rating), _iso_date(changed)


def _probability_of_default(raw: dict[str, Any]) -> str | None:
    credit_score = raw.get("creditScore") or {}
    current = credit_score.get("currentCreditRating") or {}
    pod = (
        current.get("probabilityOfDefault")
        or current.get("pod")
        or credit_score.get("probabilityOfDefault")
    )
    if pod is None:
        return None
    if isinstance(pod, dict):
        pod = pod.get("value")
    if pod is None or pod == "":
        return None
    return str(pod)


def _risk_band(raw: dict[str, Any]) -> str | None:
    credit_score = raw.get("creditScore") or {}
    current = credit_score.get("currentCreditRating") or {}
    for key in ("riskBand", "providerDescription", "creditRatingDescription"):
        val = current.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return None


def _assessment_commentary(raw: dict[str, Any]) -> str | None:
    credit_score = raw.get("creditScore") or {}
    current = credit_score.get("currentCreditRating") or {}
    for key in ("assessmentCommentary", "commentary", "assessment"):
        val = current.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    payment = raw.get("paymentData") or {}
    if isinstance(payment, dict):
        commentary = payment.get("commentary")
        if isinstance(commentary, str) and commentary.strip():
            return commentary.strip()
    return None


def parse_creditsafe_report(
    connect_id: str,
    raw: dict[str, Any],
    user_id: str | None,
) -> dict[str, Any]:
    """Parse a raw Creditsafe credit report into ``OrgCreditReport`` column values.

    ``raw`` is the report payload from ``run_credit_assessment``.
    Returns a dict whose keys match the ORM model columns so it can be
    fed straight into ``OrgCreditReportRepository.upsert_for_org``.
    """
    summary = raw.get("companySummary") or {}
    credit_score_section = raw.get("creditScore") or {}
    current_rating = credit_score_section.get("currentCreditRating") or {}
    provider_value = current_rating.get("providerValue") or {}
    credit_limit = current_rating.get("creditLimit") or {}
    identification = raw.get("companyIdentification") or {}
    basic_info = identification.get("basicInformation") or {}
    contact = raw.get("contactInformation") or {}
    main_address = contact.get("mainAddress") or {}
    classifications = identification.get("activityClassifications") or []
    first_classification = classifications[0] if classifications else {}

    prev_rating, prev_changed_at = _previous_rating(raw)

    trading_name = summary.get("businessName") or basic_info.get("registeredCompanyName")
    legal_entity_name = basic_info.get("registeredCompanyName") or trading_name

    vat_candidates = [
        basic_info.get("vatRegistrationNumber"),
        summary.get("vatNumber"),
        (contact.get("vatNumber") if isinstance(contact, dict) else None),
    ]
    vat_number = next((_to_str(v) for v in vat_candidates if _to_str(v)), None)

    company_status_raw = summary.get("companyStatus") or basic_info.get("companyStatus")
    if isinstance(company_status_raw, dict):
        company_status = _to_str(company_status_raw.get("status"))
    else:
        company_status = _to_str(company_status_raw)

    phones = contact.get("telephoneNumbers") or contact.get("phoneNumbers") or []
    contact_number: str | None = None
    if isinstance(phones, list) and phones:
        first = phones[0]
        if isinstance(first, dict):
            contact_number = _to_str(first.get("number") or first.get("telephoneNumber"))
        elif isinstance(first, str):
            contact_number = _to_str(first)
    if not contact_number:
        contact_number = _to_str(contact.get("mainPhoneNumber"))

    return {
        "connect_id": connect_id,
        "credit_score": _to_int(provider_value.get("value")),
        "credit_score_max": _to_int(provider_value.get("maxValue")),
        "credit_rating": current_rating.get("commonValue"),
        "credit_rating_description": current_rating.get("commonDescription"),
        "recommended_credit_limit": credit_limit.get("value"),
        "recommended_credit_limit_currency": credit_limit.get("currency"),

        "previous_credit_rating": prev_rating,
        "previous_rating_changed_at": prev_changed_at,
        "risk_band": _risk_band(raw),
        "probability_of_default_12m": _probability_of_default(raw),
        "assessment_commentary": _assessment_commentary(raw),

        "company_name": trading_name,
        "legal_entity_name": legal_entity_name,
        "company_status": company_status,
        "company_registration_number": summary.get("companyRegistrationNumber")
        or basic_info.get("companyRegistrationNumber"),
        "date_of_incorporation": _iso_date(
            basic_info.get("companyRegistrationDate") or basic_info.get("dateOfIncorporation"),
        ),
        "country": summary.get("country") or basic_info.get("country"),
        "latest_turnover": (summary.get("latestTurnoverFigure") or {}).get("value"),
        "latest_turnover_currency": (summary.get("latestTurnoverFigure") or {}).get("currency"),
        "registered_address": _format_address(main_address),
        "industry_code": first_classification.get("code"),
        "industry_description": first_classification.get("classification"),
        "vat_number": vat_number,
        "contact_number": contact_number,

        "last_checked_at": datetime.now(UTC),
        "checked_by_user_id": user_id,

        "directors": build_directors(raw),
        "risk_indicators": build_risk_indicators(raw),
        "payment_behaviour_description": _payment_behaviour_description(raw),
        "raw_report": raw,
    }
