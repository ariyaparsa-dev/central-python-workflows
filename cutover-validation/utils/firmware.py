"""
Firmware helper functions.
"""


def get_firmware_lookup(central_conn):

    firmware_lookup = {}

    response = central_conn.command(
        api_method="GET",
        api_path="network-services/v1/firmware-details",
    )

    if response.get("code") != 200:
        return firmware_lookup

    data = response.get("msg", {})

    for item in data.get("items", []):

        serial = item.get("serialNumber")
        firmware = item.get("firmwareVersion")

        if serial:

            firmware_lookup[serial] = (
                firmware if firmware else "N/A"
            )

    return firmware_lookup
