#!/usr/bin/env python3
"""
Device Command Validation and Execution Script

This script validates commands against device and executes them in a batch.
Supports separate YAML files for devices and troubleshooting commands.
Supports CSV input for device lists.
"""

import io
import sys
import argparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import List
from pycentral import NewCentralBase
from utils.firmware import get_firmware_lookup

# Import from utils modules
from utils.models import Device, CommandResult
from utils.config import (
    SEPARATOR_WIDTH,
    MAX_CONCURRENT_DEVICE_EXECUTIONS,
)
from utils.loaders import (
    load_commands,
    load_device_serials_from_yaml,
    load_device_serials_from_csv,
)
from utils.device_fetcher import fetch_devices_parallel, fetch_sites_and_devices
from utils.commands import validate_commands, execute_commands_sequentially
from utils.tables import (
    display_device_status_summary,
    display_site_table,
)
from utils.user_input import prompt_confirmation, prompt_site_selection
from utils.report_generators import generate_all_reports

_print_lock = threading.Lock()

def process_single_device(
    device_serial: str,
    commands: List[str],
    central_conn: NewCentralBase,
    progress_data=None,

) -> List[CommandResult]:
    """Process a single device and execute commands, buffering output for atomic printing."""
    buf = io.StringIO()

    def log(*args, **kwargs):
        print(*args, **kwargs, file=buf)

    log(f"\n{'=' * SEPARATOR_WIDTH}")
    log(f"Device: {device_serial}")
    log(f"{'=' * SEPARATOR_WIDTH}")

    results = []


    if progress_data is not None:
        progress_data["current_device"] = device_serial

    try:
        device_instance = central_conn.scopes.find_device(device_serials=device_serial)
        if not device_instance:
            log(
                f"Device with serial '{device_serial}' not found in the account. Skipping..."
            )
        else:
            # Safety check: Skip offline devices
            device_status = getattr(device_instance, "status", None) or "UNKNOWN"
            device_site = getattr(device_instance, "site_name", None)
            if not device_site:
                log(
                    f"Device with serial '{device_serial}' is not assigned to a site. Assign it to a site before troubleshooting. Skipping..."
                )
                return results

            device_status = device_status.upper()
            if device_status != "ONLINE":
                log(
                    f"Device with serial '{device_serial}' is {device_status}. Cannot execute commands. Skipping..."
                )
            else:
                # Validate commands
                validation_results = validate_commands(
                    commands, device_instance, log=log
                )

                valid_commands = [
                    cmd for cmd, is_valid in validation_results.items() if is_valid
                ]
                invalid_commands = [
                    cmd for cmd, is_valid in validation_results.items() if not is_valid
                ]

                log(f"\nValidation Summary for {device_serial}:")
                log(f"  Valid commands: {len(valid_commands)}")
                log(f"  Invalid commands: {len(invalid_commands)}")

                if invalid_commands:
                    log("\nInvalid commands will be skipped:")
                    for cmd in invalid_commands:
                        log(f"  - {cmd}")

                if not valid_commands:
                    log(
                        f"\nNo valid commands to execute for device {device_serial}. Skipping..."
                    )
                else:
                    # Execute valid commands
                    results = execute_commands_sequentially(
                        valid_commands, device_instance, log=log
                    )

                    # Add device serial to results
                    for result in results:
                        result.device_serial = device_serial

                    log(
                        f"\nCompleted processing device {device_serial}: {len(results)} commands executed"
                    )

    except Exception as e:
        log(f"Error processing device {device_serial}: {str(e)}")
    finally:
        with _print_lock:
            print(buf.getvalue(), end="")

    return results


def process_all_devices(
    device_serials: List[str], commands: List[str], central_conn, max_workers: int,progress_data=None,
) -> List[CommandResult]:
    """Process all devices in parallel with bounded worker concurrency."""
    all_results = []
    total_devices = len(device_serials)
    max_workers = min(max_workers, total_devices)

    print(f"\n{'=' * SEPARATOR_WIDTH}")
    print(
        f"Processing {total_devices} device(s) in parallel "
        f"(up to {max_workers} at a time)..."
    )
    print(f"{'=' * SEPARATOR_WIDTH}\n")



    with ThreadPoolExecutor(max_workers=max_workers) as executor:

            future_to_serial = {
                executor.submit(
                    process_single_device,
                    serial,
                    commands,
                    central_conn,
                    progress_data,
                ): serial
                for serial in device_serials
            }

            for future in as_completed(future_to_serial):

                serial = future_to_serial[future]

                try:

                    results = future.result()

                    if progress_data is not None:

                        progress_data["completed"] += 1
                        progress_data["successful"] += 1
                        progress_data["current_device"] = serial

                    all_results.extend(results)

                except Exception as e:

                    if progress_data is not None:

                        progress_data["completed"] += 1
                        progress_data["failed"] += 1
                        progress_data["current_device"] = serial

                    print(
                        f"Error processing device {serial} in worker: {str(e)}"
                    )

    return all_results


def save_results(results: List[CommandResult], devices: List[Device]) -> None:
    """Save results and generate reports."""
    device_results: dict = {}
    for result in results:
        if result.device_serial:
            device_results.setdefault(result.device_serial, []).append(result)

    device_info_map = {device.serial: device for device in devices}

    devices_overview = []
    device_entries = []
    for serial in sorted(device_results.keys()):
        device = device_info_map.get(serial, Device(serial=serial))
        cmds = device_results[serial]
        devices_overview.append({**device.to_dict(), "commands_executed": len(cmds)})
        device_entries.append(
            {
                "type": "device_results",
                "device_serial": serial,
                "device_info": device.to_dict(),
                "commands_executed": len(cmds),
                "troubleshooting_results": [r.to_dict() for r in cmds],
            }
        )

    output_data = [
        {
            "type": "summary",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "total_devices": len(device_results),
            "total_commands_executed": len(results),
            "devices_overview": devices_overview,
        },
        *device_entries,
    ]

    
    report_folder = generate_all_reports(output_data)

    print(f"  Total devices: {len(device_results)}")
    print(f"  Total commands executed: {len(results)}")
    
    return report_folder

def parse_args():
    """Parse and return command-line arguments."""
    parser = argparse.ArgumentParser(description="Cutover Validation Script")
    parser.add_argument(
        "-c",
        "--credentials",
        help="Credentials file for Central API (JSON or YAML format)",
        required=True,
    )
    parser.add_argument(
        "-d",
        "--devices",
        help="YAML or CSV file containing device serial numbers (if not provided, site selection will be prompted)",
        required=False,
    )
    parser.add_argument(
        "-t",
        "--troubleshooting_commands",
        help="YAML file containing troubleshooting commands to run on all devices",
        required=True,
    )

    parser.add_argument(
        "--max-workers",
        type=int,
        default=MAX_CONCURRENT_DEVICE_EXECUTIONS,
        help=f"Maximum number of concurrent device executions (default: {MAX_CONCURRENT_DEVICE_EXECUTIONS})",
    )
    return parser.parse_args()


def get_sites_for_selection(credentials_file: str):
    """
    Connect to Central and return sites/devices data for web-based site selection.
    Used by Flask before running validation.
    """
    print("\nConnecting to Central...")
    central_conn = NewCentralBase(
        token_info=credentials_file,
        enable_scope=True,
        log_level="ERROR",
    )

    print("Fetching all sites and devices...")
    sites_data = fetch_sites_and_devices(central_conn)

    if not sites_data:
        raise RuntimeError("No sites with online devices found in the account.")

    return sites_data


def run_validation(
    credentials_file: str,
    troubleshooting_commands_file: str,
    devices_file: str | None = None,
    selected_site_ids: list[str] | None = None,
    max_workers: int = MAX_CONCURRENT_DEVICE_EXECUTIONS,
    skip_confirmation: bool = True,
    progress_data: dict | None = None,
):
    """
    Run validation from Python/web app instead of CLI.

    Supports:
    - Device YAML/CSV file mode
    - Web-based selected site mode
    - Original CLI interactive site selection mode
    """

    # Load troubleshooting commands
    try:
        commands = load_commands(troubleshooting_commands_file)
    except Exception as e:
        raise RuntimeError(f"Error loading troubleshooting commands: {str(e)}")

    print(f"Loaded {len(commands)} troubleshooting command(s)")

    # Connect to API
    print("\nConnecting to Central...")

    try:
        central_conn = NewCentralBase(
            token_info=credentials_file,
            enable_scope=True,
            log_level="ERROR",
        )
    except Exception as e:
        raise RuntimeError(f"Error connecting to Central: {str(e)}")
    print("Loading firmware details...")

    firmware_lookup = get_firmware_lookup(
        central_conn
    )

    print(
        f"Retrieved firmware for "
        f"{len(firmware_lookup)} devices"
    )


    # ------------------------------------------------------------
    # Mode 1: Device file mode - YAML or CSV uploaded
    # ------------------------------------------------------------
    if devices_file:
        is_csv_input = devices_file.lower().endswith(".csv")

        if is_csv_input:
            print(f"Loading device serials from CSV: {devices_file}")
            device_serials = load_device_serials_from_csv(devices_file)
        else:
            print(f"Loading device serials from YAML: {devices_file}")
            device_serials = load_device_serials_from_yaml(devices_file)

        print(f"Loaded {len(device_serials)} device serial(s)")

        fetch_result = fetch_devices_parallel(device_serials, central_conn)

        display_device_status_summary(fetch_result)

        device_serials = [device.serial for device in fetch_result.online]

        if not fetch_result.has_actionable_devices:
            if fetch_result.unassigned:
                raise RuntimeError(
                    "Devices were found, but none are assigned to a site. "
                    "Assign the devices to a site in Central before troubleshooting."
                )
            else:
                raise RuntimeError("No online devices available for troubleshooting.")

        devices_for_save = fetch_result.online
        for device in devices_for_save:

            device.firmware = firmware_lookup.get(
             device.serial,
             "N/A"
            )

    # ------------------------------------------------------------
    # Mode 2 or 3: Site selection mode
    # ------------------------------------------------------------
    else:
        print("\nNo device file provided. Using site selection mode...\n")
        print("Fetching all sites and devices...")

        sites_data = fetch_sites_and_devices(central_conn)

        if not sites_data:
            raise RuntimeError("No sites with online devices found in the account.")

        display_site_table(sites_data)

        # Convert site keys to strings because HTML forms submit values as strings
        site_key_lookup = {str(site_id): site_id for site_id in sites_data.keys()}

        # Mode 2: Flask/web selected sites
        if selected_site_ids is not None:
            if not selected_site_ids:
                raise RuntimeError("No sites were selected.")

            selected_site_ids = [
                site_key_lookup[site_id]
                for site_id in selected_site_ids
                if site_id in site_key_lookup
            ]

            if not selected_site_ids:
                raise RuntimeError("Selected site IDs do not match available sites.")

            print(f"\nSelected {len(selected_site_ids)} site(s) from web UI.")

        # Mode 3: Original CLI interactive prompt
        else:
            if skip_confirmation:
                # Fallback for non-interactive use
                selected_site_ids = list(sites_data.keys())
                print(f"\nAutomatically selected all {len(selected_site_ids)} site(s).")
            else:
                selected_site_ids = prompt_site_selection(sites_data,firmware_lookup)

        device_serials = [
            serial
            for site_id in selected_site_ids
            if site_id in sites_data
            for serial in sites_data[site_id]["online_serials"]
        ]

        if not device_serials:
            raise RuntimeError("No online devices found in the selected site(s).")

        print(f"\nFound {len(device_serials)} online device(s) in selected site(s).")

        devices_for_save = []

        for site_id in selected_site_ids:
            if site_id in sites_data:
                devices_for_save.extend(
                    sites_data[site_id]["online_device_details"]
                )
        for device in devices_for_save:

            device.firmware = firmware_lookup.get(
            device.serial,
            "N/A"
            )

    # ------------------------------------------------------------
    # Confirmation
    # ------------------------------------------------------------
    if not skip_confirmation:
        if not prompt_confirmation(device_serials, commands):
            print("Execution cancelled by user.")
            return []

    # ------------------------------------------------------------
    # Process devices
    # ------------------------------------------------------------

    if progress_data is not None:

        progress_data["total"] = len(device_serials)
        progress_data["completed"] = 0
        progress_data["successful"] = 0
        progress_data["failed"] = 0
        progress_data["current_device"] = ""
        progress_data["status"] = "running"


    all_results = process_all_devices(
        device_serials,
        commands,
        central_conn,
        max_workers,
        progress_data,
    )

    # ------------------------------------------------------------
    # Save reports
    # ------------------------------------------------------------
    if all_results:
        
        report_folder = save_results(
            all_results,
            devices_for_save
        )

        print(
            f"\nExecution completed. Processed {len(all_results)} total commands "
            f"across {len(device_serials)} devices."
        )
    else:
        print("\nNo commands were executed successfully.")

    if progress_data is not None:
        progress_data["status"] = "completed"

    return {
        "results": all_results,
        "report_folder": report_folder,
    }



def main():
    args = parse_args()

    run_validation(
        credentials_file=args.credentials,
        troubleshooting_commands_file=args.troubleshooting_commands,
        devices_file=args.devices,
        selected_site_ids=None,
        max_workers=args.max_workers,
        skip_confirmation=False,
    )


if __name__ == "__main__":
    main()
