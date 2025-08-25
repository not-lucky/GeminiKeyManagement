"""Manages persistent storage of API key metadata.

Implements:
- JSON schema validation
- Thread-safe database operations
- Key lifecycle tracking
- Data versioning and backup
"""
import os
import json
import logging
import sys
from datetime import datetime, timezone
import jsonschema
from . import config

def load_schema(filename):
    """Validates and loads JSON schema definition.
    
    Args:
        filename (str): Path to schema file
    
    Returns:
        dict: Parsed schema document
    
    Exits:
        SystemExit: On invalid schema file
    """
    if not os.path.exists(filename):
        logging.error(f"Schema file not found at '{filename}'")
        sys.exit(1)
    with open(filename, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            logging.error(f"Could not decode JSON schema from {filename}.")
            sys.exit(1)

def load_keys_database(filename, schema):
    """Loads and validates the JSON database of API keys."""
    if not os.path.exists(filename):
        return {
            "schema_version": "1.0.0",
            "accounts": []
        }
    with open(filename, "r") as f:
        try:
            data = json.load(f)
            jsonschema.validate(instance=data, schema=schema)
            return data
        except json.JSONDecodeError:
            logging.warning(f"Could not decode JSON from {filename}. Starting fresh.")
        except jsonschema.ValidationError as e:
            logging.warning(f"Database file '{filename}' is not valid. {e.message}. Starting fresh.")
        
        return {
            "schema_version": "1.0.0",
            "accounts": []
        }

def save_keys_to_json(data, filename, schema):
    """Validates and saves the API key data to a single JSON file."""
    now = datetime.now(timezone.utc).isoformat()
    data["generation_timestamp_utc"] = data.get("generation_timestamp_utc", now)
    data["last_modified_utc"] = now
    try:
        jsonschema.validate(instance=data, schema=schema)
        with open(filename, "w") as f:
            json.dump(data, f, indent=2)
        logging.info(f"--- Database saved to {filename} ---")
    except jsonschema.ValidationError as e:
        logging.error(f"Data to be saved is invalid. Could not write to '{filename}'.")
        logging.error(f"Validation Error: {e.message}")
        sys.exit(1)

def add_key_to_database(account_entry, project, key_object):
    """Adds a new API key's details to the data structure."""
    project_id = project.project_id

    project_entry = next((p for p in account_entry["projects"] if p.get("project_info", {}).get("project_id") == project_id), None)
    if not project_entry:
        project_entry = {
            "project_info": {
                "project_id": project_id,
                "project_name": project.display_name,
                "project_number": project.name.split('/')[-1],
                "state": str(project.state)
            },
            "api_keys": []
        }
        account_entry["projects"].append(project_entry)

    api_targets = []
    if key_object.restrictions and key_object.restrictions.api_targets:
        for target in key_object.restrictions.api_targets:
            api_targets.append({"service": target.service, "methods": []})

    new_key_entry = {
        "key_details": {
            "key_string": key_object.key_string,
            "key_id": key_object.uid,
            "key_name": key_object.name,
            "display_name": key_object.display_name,
            "creation_timestamp_utc": key_object.create_time.isoformat(),
            "last_updated_timestamp_utc": key_object.update_time.isoformat(),
        },
        "restrictions": {
            "api_targets": api_targets
        },
        "state": "ACTIVE"
    }

    existing_key = next((k for k in project_entry["api_keys"] if k.get("key_details", {}).get("key_id") == key_object.uid), None)
    if not existing_key:
        project_entry["api_keys"].append(new_key_entry)
        logging.info(f"  Added key {key_object.uid} to local database for project {project_id}")
    else:
        logging.warning(f"  Key {key_object.uid} already exists in local database for project {project_id}")

def remove_keys_from_database(account_entry, project_id, deleted_keys_uids):
    """Removes deleted API keys from the data structure."""
    project_entry = next((p for p in account_entry["projects"] if p.get("project_info", {}).get("project_id") == project_id), None)
    if not project_entry:
        return

    initial_key_count = len(project_entry["api_keys"])
    project_entry["api_keys"] = [
        key for key in project_entry["api_keys"]
        if key.get("key_details", {}).get("key_id") not in deleted_keys_uids
    ]
    final_key_count = len(project_entry["api_keys"])
    num_removed = initial_key_count - final_key_count
    if num_removed > 0:
        logging.info(f"  Removed {num_removed} key(s) from local database for project {project_id}")