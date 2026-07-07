"""Load devices and commands from YAML/CSV files."""

import csv
import sys
import yaml
from typing import List, Any, Optional, Dict
from utils.config import CSV_SERIAL_COLUMNS, CSV_SAMPLE_SIZE, MAX_COMMANDS_SUPPORTED


def load_yaml_file(yaml_file: str, key: Optional[str] = None) -> Any:
    """Generic YAML loader with optional key extraction."""
    try:
        with open(yaml_file, "r") as f:
            data = yaml.safe_load(f)

        if key:
            if isinstance(data, dict) and key in data:
                return data[key]
            raise ValueError(f"Expected '{key}' key in YAML file")

        return data
    except Exception as e:
        print(f"Error loading YAML file: {e}")
        sys.exit(1)


def load_commands(yaml_file: str) -> Dict[str, List[str]]:
    """
    Load troubleshooting commands from YAML file.

    Expected format:

    ap:
      - show ap debug cloud-server
      - show ap association
      - show version

    cx:
      - show version
      - show vlan
      - show interface brief

    gateway:
      - show version
      - show datapath session
    """

    data = load_yaml_file(yaml_file)

    if not isinstance(data, dict) or not data:
        raise ValueError(
            "Invalid YAML format. Expected device type sections such as "
            "'ap', 'cx', and/or 'gateway'."
        )

    command_config = {}
    total_commands = 0

    for device_type, commands in data.items():
        if not isinstance(device_type, str) or not device_type.strip():
            raise ValueError(f"Invalid device type section: {device_type}")

        device_type_key = device_type.strip().lower()

        if not isinstance(commands, list) or not commands:
            raise ValueError(
                f"No valid commands found for device type '{device_type_key}'."
            )

        cleaned_commands = []

        for cmd in commands:
            if not isinstance(cmd, str) or not cmd.strip():
                raise ValueError(
                    f"Invalid command entry under '{device_type_key}': {cmd}. "
                    "All commands must be non-empty strings."
                )

            s = cmd.strip()

            if not s.lower().startswith("show "):
                raise ValueError(
                    f"Invalid command under '{device_type_key}': '{cmd}'. "
                    "All commands must start with 'show '."
                )

            cleaned_commands.append(s)

        command_config[device_type_key] = cleaned_commands
        total_commands += len(cleaned_commands)

    if total_commands > MAX_COMMANDS_SUPPORTED:
        raise ValueError(
            f"Too many commands. Maximum supported is {MAX_COMMANDS_SUPPORTED}. "
            f"Configured total is {total_commands}."
        )

    return command_config


def load_device_serials_from_yaml(yaml_file: str) -> List[str]:
    """Load device serial numbers from YAML file."""
    data = load_yaml_file(yaml_file)
    device_serials = []

    if isinstance(data, dict) and "devices" in data:
        device_serials = data["devices"]
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                device_serials.append(item)
            elif isinstance(item, dict) and "device_serial" in item:
                device_serials.append(item["device_serial"])
            else:
                print(f"Warning: Skipping invalid device entry: {item}")
    else:
        raise ValueError(
            "Invalid YAML format. Expected 'devices' key or list of device serials"
        )

    if not device_serials:
        raise ValueError("No valid device serials found in YAML file")

    return device_serials


def load_device_serials_from_csv(csv_file: str) -> List[str]:
    """Load device serial numbers from CSV file."""
    try:
        device_serials = []
        with open(csv_file, "r") as f:
            sample = f.read(CSV_SAMPLE_SIZE)
            f.seek(0)

            has_header = csv.Sniffer().has_header(sample)
            reader = csv.reader(f)

            serial_col_idx = 0  # Default to first column

            if has_header:
                headers = [h.lower() for h in next(reader)]
                # Look for serial column
                for idx, header in enumerate(headers):
                    if header in CSV_SERIAL_COLUMNS:
                        serial_col_idx = idx
                        break
                else:
                    print("Warning: No serial number column found. Using first column.")

            for row in reader:
                if row and len(row) > serial_col_idx and row[serial_col_idx].strip():
                    device_serials.append(row[serial_col_idx].strip())

        if not device_serials:
            raise ValueError("No device serials found in CSV file")

        return device_serials

    except Exception as e:
        print(f"Error loading devices from CSV: {e}")
        sys.exit(1)
