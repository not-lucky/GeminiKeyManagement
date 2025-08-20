import os
import sys
import argparse
import json
import logging
import threading
import concurrent.futures
from datetime import datetime, timezone
import jsonschema
import google.auth
from colorama import Fore, Style, init
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.cloud import resourcemanager_v3, service_usage_v1, api_keys_v2
from google.api_core import exceptions as google_exceptions
from google.auth.transport import requests

# --- CONFIGURATION ---
CREDENTIALS_DIR = "credentials"
EMAILS_FILE = "emails.txt"
CLIENT_SECRETS_FILE = "credentials.json"
API_KEYS_DATABASE_FILE = "api_keys_database.json"
API_KEYS_SCHEMA_FILE = os.path.join("schemas", "v1", "api_keys_database.schema.json")
LOG_DIR = "logs"
# ---------------------

# --- LOGGING SETUP ---
class ColoredFormatter(logging.Formatter):
    """A custom logging formatter that adds color to console output."""

    LOG_COLORS = {
        logging.DEBUG: Fore.CYAN,
        logging.INFO: Fore.GREEN,
        logging.WARNING: Fore.YELLOW,
        logging.ERROR: Fore.RED,
        logging.CRITICAL: Fore.RED + Style.BRIGHT,
    }

    def format(self, record):
        """Formats the log record with appropriate colors."""
        color = self.LOG_COLORS.get(record.levelno)
        message = super().format(record)
        if color:
            # Only color the message part for readability
            parts = message.split(" - ", 2)
            if len(parts) > 2:
                parts[2] = color + parts[2] + Style.RESET_ALL
                message = " - ".join(parts)
            else:
                message = color + message + Style.RESET_ALL
        return message

def setup_logging():
    """Sets up logging to both console and a file, with colors for the console."""
    init(autoreset=True) # Initialize Colorama

    if not os.path.exists(LOG_DIR):
        os.makedirs(LOG_DIR)

    log_filename = f"gemini_key_management_{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H-%M-%S')}.log"
    log_filepath = os.path.join(LOG_DIR, log_filename)

    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # Clear existing handlers to avoid duplicate logs
    if logger.hasHandlers():
        logger.handlers.clear()

    # File handler for detailed, non-colored logging
    file_handler = logging.FileHandler(log_filepath, encoding='utf-8')
    file_formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - [%(name)s:%(module)s:%(lineno)d] - %(message)s"
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Console handler for concise, colored logging
    console_handler = logging.StreamHandler(sys.stdout)
    console_formatter = ColoredFormatter("%(asctime)s - %(levelname)s - %(message)s")
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    logging.info(f"Logging initialized. Log file: {log_filepath}")

setup_logging()
# ---------------------


SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
]

def load_schema(filename):
    """Loads a JSON schema from a file."""
    if not os.path.exists(filename):
        logging.error(f"Schema file not found at '{filename}'")
        sys.exit(1)
    with open(filename, "r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            logging.error(f"Could not decode JSON schema from {filename}.")
            sys.exit(1)

def load_emails_from_file(filename):
    """Loads a list of emails from a text file, ignoring comments."""
    if not os.path.exists(filename):
        logging.error(f"Email file not found at '{filename}'")
        logging.info("Please create it and add one email address per line.")
        return []
    with open(filename, "r") as f:
        # Ignore empty lines and lines starting with #
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]

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


def main():
    """Main function to orchestrate API key creation or deletion."""
    parser = argparse.ArgumentParser(description="Manage Gemini API keys in Google Cloud projects.")
    parser.add_argument("action", choices=['create', 'delete'], help="The action to perform: 'create' or 'delete' API keys.")
    parser.add_argument("--email", help="Specify a single email address to process. Required for 'delete'. If not provided for 'create', emails will be read from emails.txt.")
    parser.add_argument("--dry-run", action="store_true", help="Simulate the run without making any actual changes to Google Cloud resources.")
    args = parser.parse_args()

    if args.action == 'delete' and not args.email:
        parser.error("the --email argument is required for the 'delete' action")

    if not os.path.exists(CLIENT_SECRETS_FILE):
        logging.error(f"OAuth client secrets file not found at '{CLIENT_SECRETS_FILE}'")
        logging.error("Please follow the setup instructions in README.md to create it.")
        sys.exit(1)

    if not os.path.exists(CREDENTIALS_DIR):
        os.makedirs(CREDENTIALS_DIR)

    schema = load_schema(API_KEYS_SCHEMA_FILE)
    api_keys_data = load_keys_database(API_KEYS_DATABASE_FILE, schema)

    emails_to_process = []
    if args.email:
        emails_to_process.append(args.email)
    elif args.action == 'delete':
        logging.error("The 'delete' action requires the --email argument to specify which account's keys to delete.")
        sys.exit(1)
    else: # action is 'create' and no email provided
        emails_to_process = load_emails_from_file(EMAILS_FILE)
        if not emails_to_process:
            logging.info("No emails found in emails.txt. Exiting.")
            sys.exit(1)

    for email in emails_to_process:
        process_account(email, args.action, api_keys_data, args.dry_run)

    if not args.dry_run:
        save_keys_to_json(api_keys_data, API_KEYS_DATABASE_FILE, schema)

def process_project_for_action(project, creds, action, dry_run, db_lock, account_entry):
    """Processes a single project for the given action in a thread-safe manner."""
    project_id = project.project_id
    logging.info(f"- Starting to process project: {project_id} ({project.display_name})")

    if action == 'create':
        if project_has_gemini_key(project_id, creds):
            logging.info(f"  'Gemini API Key' already exists in project {project_id}. Skipping creation.")
            return

        if enable_api(project_id, creds, dry_run=dry_run):
            key_object = create_api_key(project_id, creds, dry_run=dry_run)
            if key_object:
                with db_lock:
                    add_key_to_database(account_entry, project, key_object)
    elif action == 'delete':
        deleted_keys_uids = delete_api_keys(project_id, creds, dry_run=dry_run)
        if deleted_keys_uids:
            with db_lock:
                remove_keys_from_database(account_entry, project_id, deleted_keys_uids)
    logging.info(f"- Finished processing project: {project_id}")


def process_account(email, action, api_keys_data, dry_run=False, max_workers=5):
    """Processes a single account for the given action."""
    logging.info(f"--- Processing account: {email} for action: {action} ---")
    if dry_run:
        logging.info("*** DRY RUN MODE ENABLED ***")

    creds = get_credentials_for_email(email)
    if not creds:
        logging.warning(f"Could not get credentials for {email}. Skipping.")
        return

    account_entry = next((acc for acc in api_keys_data["accounts"] if acc.get("account_details", {}).get("email") == email), None)
    if not account_entry:
        account_entry = {
            "account_details": {
                "email": email,
                "authentication_details": {
                    "token_file": os.path.join(CREDENTIALS_DIR, f"{email}.json"),
                    "scopes": SCOPES
                }
            },
            "projects": []
        }
        api_keys_data["accounts"].append(account_entry)

    try:
        resource_manager = resourcemanager_v3.ProjectsClient(credentials=creds)
        projects = list(resource_manager.search_projects())

        if not projects:
            logging.info(f"No projects found for {email}.")
            return

        logging.info(f"Found {len(projects)} projects. Processing with up to {max_workers} workers...")
        
        db_lock = threading.Lock()

        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_project = {
                executor.submit(process_project_for_action, project, creds, action, dry_run, db_lock, account_entry): project
                for project in projects
            }
            for future in concurrent.futures.as_completed(future_to_project):
                project = future_to_project[future]
                try:
                    future.result()
                except Exception as exc:
                    logging.error(f"Project {project.project_id} generated an exception: {exc}", exc_info=True)

    except google_exceptions.PermissionDenied as err:
        logging.error(f"Permission denied for account {email}. Check IAM roles.")
        logging.error(f"  Error: {err}")
    except google_exceptions.GoogleAPICallError as err:
        logging.error(f"An API error occurred while processing account {email}: {err}")

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

def project_has_gemini_key(project_id, credentials):
    """Checks if a project already has a key named 'Gemini API Key'."""
    try:
        api_keys_client = api_keys_v2.ApiKeysClient(credentials=credentials)
        parent = f"projects/{project_id}/locations/global"
        keys = api_keys_client.list_keys(parent=parent)
        for key in keys:
            if key.display_name in ["Gemini API Key", "Generative Language API Key"]: # 2nd name if when api created using AI studio
                return True
        return False
    except google_exceptions.GoogleAPICallError as err:
        logging.error(f"  Could not list keys in project {project_id}. Error: {err}")
        return False

def get_credentials_for_email(email):
    """Handles the OAuth2 flow for a given email."""
    token_file = os.path.join(CREDENTIALS_DIR, f"{email}.json")
    creds = None
    try:
        if os.path.exists(token_file):
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                logging.info("Refreshing credentials...")
                creds.refresh(google.auth.transport.requests.Request())
            else:
                logging.info(f"Please authenticate with: {email}")
                flow = InstalledAppFlow.from_client_secrets_file(
                    CLIENT_SECRETS_FILE, SCOPES
                )
                creds = flow.run_local_server(port=0, login_hint=email)
            with open(token_file, "w") as token:
                token.write(creds.to_json())
        return creds
    except Exception as e:
        logging.error(f"An unexpected error occurred during authentication: {e}")
        return None

def enable_api(project_id, credentials, dry_run=False):
    """Enables the Generative Language API if it's not already enabled."""
    service_name = "generativelanguage.googleapis.com"
    service_path = f"projects/{project_id}/services/{service_name}"
    service_usage_client = service_usage_v1.ServiceUsageClient(credentials=credentials)

    try:
        service_request = service_usage_v1.GetServiceRequest(name=service_path)
        service = service_usage_client.get_service(request=service_request)

        if service.state == service_usage_v1.State.ENABLED:
            logging.info(f"  Generative Language API is already enabled for project {project_id}")
            return True

        logging.info(f"  API is not enabled. Attempting to enable...")
        if dry_run:
            logging.info(f"  [DRY RUN] Would enable API for project {project_id}")
            return True

        enable_request = service_usage_v1.EnableServiceRequest(name=service_path)
        operation = service_usage_client.enable_service(request=enable_request)
        logging.info("  Waiting for API enablement to complete...")
        operation.result()
        logging.info(f"  Successfully enabled Generative Language API for project {project_id}")
        return True

    except google_exceptions.PermissionDenied:
        logging.warning(f"  Permission denied to check or enable API for project {project_id}. Skipping.")
        return False
    except google_exceptions.GoogleAPICallError as err:
        logging.error(f"  Error checking or enabling API for project {project_id}: {err}")
        return False

def create_api_key(project_id, credentials, dry_run=False):
    """Creates a new, restricted API key."""
    if dry_run:
        logging.info(f"  [DRY RUN] Would create API key for project {project_id}")
        # Return a mock key object for dry run
        return api_keys_v2.Key(
            name=f"projects/{project_id}/locations/global/keys/mock-key-id",
            uid="mock-key-id",
            display_name="Gemini API Key",
            key_string="mock-key-string-for-dry-run",
            create_time=datetime.now(timezone.utc),
            update_time=datetime.now(timezone.utc),
            restrictions=api_keys_v2.Restrictions(
                api_targets=[
                    api_keys_v2.ApiTarget(service="generativelanguage.googleapis.com")
                ]
            ),
        )

    try:
        api_keys_client = api_keys_v2.ApiKeysClient(credentials=credentials)
        api_target = api_keys_v2.ApiTarget(service="generativelanguage.googleapis.com")
        key = api_keys_v2.Key(
            display_name="Gemini API Key",
            restrictions=api_keys_v2.Restrictions(api_targets=[api_target]),
        )
        request = api_keys_v2.CreateKeyRequest(
            parent=f"projects/{project_id}/locations/global",
            key=key,
        )
        logging.info("  Creating API key...")
        operation = api_keys_client.create_key(request=request)
        result = operation.result()
        logging.info(f"  Successfully created restricted API key for project {project_id}")
        return result
    except google_exceptions.PermissionDenied:
        logging.warning(f"  Permission denied to create API key for project {project_id}. Skipping.")
        return None
    except google_exceptions.GoogleAPICallError as err:
        logging.error(f"  Error creating API key for project {project_id}: {err}")
        return None

def delete_api_keys(project_id, credentials, dry_run=False):
    """Deletes all API keys with the display name 'Gemini API Key' and returns their UIDs."""
    deleted_keys_uids = []
    try:
        api_keys_client = api_keys_v2.ApiKeysClient(credentials=credentials)
        parent = f"projects/{project_id}/locations/global"

        keys = api_keys_client.list_keys(parent=parent)
        keys_to_delete = [key for key in keys if key.display_name == "Gemini API Key"]

        if not keys_to_delete:
            logging.info(f"  No 'Gemini API Key' found to delete.")
            return []

        logging.info(f"  Found {len(keys_to_delete)} key(s) with display name 'Gemini API Key'. Deleting...")
        for key in keys_to_delete:
            if dry_run:
                logging.info(f"  [DRY RUN] Would delete key: {key.uid}")
                deleted_keys_uids.append(key.uid)
                continue
            try:
                request = api_keys_v2.DeleteKeyRequest(name=key.name)
                operation = api_keys_client.delete_key(request=request)
                operation.result()
                logging.info(f"  Successfully deleted key: {key.uid}")
                deleted_keys_uids.append(key.uid)
            except google_exceptions.GoogleAPICallError as err:
                logging.error(f"  Error deleting key {key.uid}: {err}")
        return deleted_keys_uids
    except google_exceptions.PermissionDenied:
        logging.warning(f"  Permission denied to list or delete API keys for project {project_id}. Skipping.")
    except google_exceptions.GoogleAPICallError as err:
        logging.error(f"  An API error occurred while deleting keys for project {project_id}: {err}")
    return []

if __name__ == "__main__":
    main()