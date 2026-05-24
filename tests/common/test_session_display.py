from app.common.session_display import (
    session_device_label,
    session_ip_location_label,
    session_ua_breakdown,
)


def test_session_device_label_for_desktop_ua() -> None:
    ua = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    label = session_device_label(ua)
    assert "Chrome" in label
    assert "Windows" in label


def test_session_ua_breakdown_for_empty_ua() -> None:
    br, os_fam, dev_fam, is_mobile, is_tablet, is_pc = session_ua_breakdown("")
    assert br is None
    assert os_fam is None
    assert dev_fam is None
    assert is_mobile is False
    assert is_tablet is False
    assert is_pc is False


def test_session_ip_location_label_skips_private_or_missing_geoip_db() -> None:
    # Private ranges should never return a public location label.
    assert session_ip_location_label("192.168.1.10") is None
