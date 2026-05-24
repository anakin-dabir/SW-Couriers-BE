import enum


class DropdownConfigKey(enum.StrEnum):
    FUEL_TYPE = "FUEL_TYPE"
    DEFECT_CATEGORY = "DEFECT_CATEGORY"
    MAINTENANCE_TYPE = "MAINTENANCE_TYPE"
    SERVICE_TYPE = "SERVICE_TYPE"
    VEHICLE_AVAILABILITY = "VEHICLE_AVAILABILITY"


_KEY_LABELS: dict[DropdownConfigKey, str] = {
    DropdownConfigKey.FUEL_TYPE: "Fuel Type",
    DropdownConfigKey.DEFECT_CATEGORY: "Defect Category",
    DropdownConfigKey.MAINTENANCE_TYPE: "Maintenance Type",
    DropdownConfigKey.SERVICE_TYPE: "Service Type",
    DropdownConfigKey.VEHICLE_AVAILABILITY: "Vehicle Availability Status",
}


def key_display_name(key: DropdownConfigKey) -> str:
    return _KEY_LABELS.get(key) or key.value.replace("_", " ").title()
