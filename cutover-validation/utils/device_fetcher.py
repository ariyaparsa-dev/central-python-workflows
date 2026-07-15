"""Parallel device fetching with ThreadPoolExecutor."""

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple
from utils.models import Device, DeviceFetchResult
from utils.config import MAX_CONCURRENT_DEVICE_FETCHES


def fetch_single_device(
    serial: str, central_conn, index: int, total: int
) -> Tuple[Device, str, str]:
    """Fetch a single device by serial number.

    Returns:
        Tuple of (device, status, error_message)
        status can be: 'online', 'offline', 'unassigned', 'not_found', 'error'
        Note: Devices without site assignment cannot report online/offline status.
    """
    try:
        print(f"  Progress: {index}/{total} - Fetching {serial}...", end="\r")
        device_instance = central_conn.scopes.find_device(device_serials=serial)

        if not device_instance:
            return None, "not_found", f"Device {serial} not found"

        device = Device.from_api_object(device_instance)

        # Devices without site assignment cannot report online/offline status
        if not device.is_assigned_to_site():
            return (
                device,
                "unassigned",
                "Device is not assigned to a site. Cannot determine online/offline status.",
            )

        if device.is_online():
            return device, "online", ""
        else:
            return device, "offline", f"Device is {device.status}"

    except Exception as e:
        return None, "error", f"Error fetching device {serial}: {e}"


def fetch_devices_parallel(
    device_serials: List[str], central_conn
) -> DeviceFetchResult:
    """Fetch device details in parallel and separate by status.

    Returns:
        DeviceFetchResult with categorized devices:
        - online: devices ready for troubleshooting
        - unassigned: devices without site (status unknown)
        - offline: devices that are offline
        - not_found: serial numbers not found in account
    """
    result = DeviceFetchResult()
    total = len(device_serials)

    max_workers = min(MAX_CONCURRENT_DEVICE_FETCHES, total)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_serial = {
            executor.submit(
                fetch_single_device, serial, central_conn, idx, total
            ): serial
            for idx, serial in enumerate(device_serials, 1)
        }

        for future in as_completed(future_to_serial):
            serial = future_to_serial[future]
            try:
                device, status, error_msg = future.result()
                if status == "online":
                    result.online.append(device)
                elif status == "unassigned":
                    result.unassigned.append(device)
                    print(f"Warning: {serial}: {error_msg}")
                elif status == "offline":
                    result.offline.append(device)
                elif status in ("not_found", "error"):
                    result.not_found.append(serial)
                    print(f"Warning: {error_msg}")

            except Exception as e:
                print(f"Exception processing {serial}: {e}")
                result.not_found.append(serial)

    print(
        f"  Online: {len(result.online)}, Unassigned: {len(result.unassigned)}, "
        f"Offline: {len(result.offline)}, Not Found: {len(result.not_found)}"
    )

    return result


def fetch_sites_and_devices(central_conn) -> dict:
    """Fetch all sites and devices from the account, filtering sites with 0 online APs."""
    try:
        sites_data = {}
      
        all_sites = list(
            central_conn.scopes.sites
        )

        
        for site in all_sites:


            site_id = site.get_id()
            site_name = site.name
            site_devices_id = site.devices

            if not site_devices_id:
                continue  # Skip sites with no devices

            # Fetch all devices for this site
            site_devices = []
            for device_id in site_devices_id:
                try:
                    device_instance = central_conn.scopes.find_device(
                        device_ids=device_id
                    )
                    if device_instance:
                        site_devices.append(device_instance)
                except Exception as e:
                    print(f"Warning: Failed to fetch device {device_id}: {e}")

            # APs
            aps = [
                d for d in site_devices
                if d.device_type == "ACCESS_POINT"
            ]

            online_aps = [
                d for d in aps
                if d.status == "ONLINE"
            ]

            # Switches
            switches = [
                d for d in site_devices
                if d.device_type == "SWITCH"
            ]

            online_switches = [
                d for d in switches
                if d.status == "ONLINE"
            ]

            # Gateways
            gateways = [
                d for d in site_devices
                if d.device_type == "GATEWAY"
            ]

            online_gateways = [
                d for d in gateways
                if d.status == "ONLINE"
            ]

            total_online_devices = (
                len(online_aps)
                + len(online_switches)
                + len(online_gateways)
            )

            # Skip sites with no online devices
            if total_online_devices == 0:
                continue

            online_device_details = [
                Device.from_api_object(device)
                for device in (
                    online_aps
                    + online_switches
                    + online_gateways
                )
            ]

            
            sites_data[site_id] = {
                "name": site_name,
                "site_id": site_id,

                "online_ap_count": len(online_aps),
                "online_switch_count": len(online_switches),
                "online_gateway_count": len(online_gateways),

                "online_count": total_online_devices,

                "offline_count":
                    len(aps)
                    + len(switches)
                    + len(gateways)
                    - total_online_devices,

                "online_serials": [
                    d.serial
                    for d in (
                        online_aps
                        + online_switches
                        + online_gateways
                    )
                ],

                "online_device_details": online_device_details,

                "total_devices": len(site_devices),
            }


        return sites_data

    except Exception as e:
            
            raise RuntimeError(
                    f"Authentication or site retrieval failed: {str(e)}"
                )

