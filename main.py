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
import random
import string
import time

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
    parser.add_argument("--max-workers", type=int, default=5, help="The maximum number of concurrent projects to process.")
    parser.add_argument("--auth-retries", type=int, default=3, help="Number of retries for a failed authentication attempt.")
    parser.add_argument("--auth-retry-delay", type=int, default=5, help="Delay in seconds between authentication retries.")
    args = parser.parse_args()

    logging.info(f"Program arguments: {vars(args)}")

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

    # --- New Authentication Logic ---
    creds_map = {}
    emails_needing_interactive_auth = []

    logging.info("Checking credentials and refreshing tokens for all accounts...")
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
        future_to_email = {executor.submit(get_and_refresh_credentials, email, max_retries=args.auth_retries, retry_delay=args.auth_retry_delay): email for email in emails_to_process}
        
        for future in concurrent.futures.as_completed(future_to_email):
            email = future_to_email[future]
            try:
                creds = future.result()
                if creds:
                    creds_map[email] = creds
                else:
                    emails_needing_interactive_auth.append(email)
            except Exception as exc:
                logging.error(f"Credential check for {email} generated an exception: {exc}", exc_info=True)
                emails_needing_interactive_auth.append(email)

    if emails_needing_interactive_auth:
        logging.info(f"\n--- INTERACTIVE AUTHENTICATION REQUIRED ---")
        logging.info(f"The following accounts require manual authentication: {', '.join(sorted(emails_needing_interactive_auth))}")
        
        for email in sorted(emails_needing_interactive_auth):
            creds = run_interactive_auth(email, max_retries=args.auth_retries, retry_delay=args.auth_retry_delay)
            if creds:
                logging.info(f"Successfully authenticated {email}.")
                creds_map[email] = creds
            else:
                logging.warning(f"Authentication failed or was cancelled for {email}. This account will be skipped.")
    
    logging.info("\n--- Credential checking complete ---")

    for email in emails_to_process:
        if email in creds_map:
            process_account(email, creds_map[email], args.action, api_keys_data, dry_run=args.dry_run, max_workers=args.max_workers)
        else:
            logging.warning(f"Skipping account {email} because authentication was not successful.")

    if not args.dry_run:
        save_keys_to_json(api_keys_data, API_KEYS_DATABASE_FILE, schema)



def sync_project_keys(project, creds, dry_run, db_lock, account_entry):
    """Synchronizes API keys between Google Cloud and the local database for a single project.
    Returns True if a Gemini API key exists in the project, False otherwise."""
    
    # Helper class to create a mock key object compatible with add_key_to_database
    class TempKey:
        def __init__(self, cloud_key, key_string):
            self.key_string = key_string
            self.uid = cloud_key.uid
            self.name = cloud_key.name
            self.display_name = cloud_key.display_name
            self.create_time = cloud_key.create_time
            self.update_time = cloud_key.update_time
            self.restrictions = cloud_key.restrictions

    project_id = project.project_id
    logging.info(f"  Synchronizing keys for project {project_id}")
    gemini_key_exists = False

    try:
        api_keys_client = api_keys_v2.ApiKeysClient(credentials=creds)
        parent = f"projects/{project_id}/locations/global"
        
        # 1. Fetch cloud keys
        cloud_keys_list = list(api_keys_client.list_keys(parent=parent))
        for key in cloud_keys_list:
            if key.display_name in ["Gemini API Key", "Generative Language API Key"]:
                gemini_key_exists = True
        
        cloud_keys = {key.uid: key for key in cloud_keys_list}
        
        # 2. Fetch local keys
        project_entry = next((p for p in account_entry["projects"] if p.get("project_info", {}).get("project_id") == project_id), None)
        
        if not project_entry:
            # If project is not in DB, create it.
            project_entry = {
                "project_info": {
                    "project_id": project.project_id,
                    "project_name": project.display_name,
                    "project_number": project.name.split('/')[-1],
                    "state": str(project.state)
                },
                "api_keys": []
            }
            with db_lock:
                account_entry["projects"].append(project_entry)
        
        local_keys = {key['key_details']['key_id']: key for key in project_entry.get('api_keys', [])}

        # 3. Reconcile
        cloud_uids = set(cloud_keys.keys())
        local_uids = set(local_keys.keys())

        synced_uids = cloud_uids.intersection(local_uids)
        cloud_only_uids = cloud_uids - local_uids
        local_only_uids = local_uids - cloud_uids

        # 4. Process
        for uid in synced_uids:
            logging.info(f"    Key {uid} is synchronized.")

        for uid in cloud_only_uids:
            key_object = cloud_keys[uid]
            logging.info(f"    Key {uid} ({key_object.display_name}) found in cloud only. Adding to local database.")
            if dry_run:
                logging.info(f"    [DRY RUN] Would fetch key string for {uid} and add to database.")
                continue
            
            try:
                # The Key object from list_keys doesn't have key_string, so we fetch it.
                key_string_response = api_keys_client.get_key_string(name=key_object.name)
                
                hydrated_key = TempKey(key_object, key_string_response.key_string)

                with db_lock:
                    add_key_to_database(account_entry, project, hydrated_key)

            except google_exceptions.PermissionDenied:
                logging.warning(f"    Permission denied to get key string for {uid}. Skipping.")
            except google_exceptions.GoogleAPICallError as err:
                logging.error(f"    Error getting key string for {uid}: {err}")

        for uid in local_only_uids:
            logging.info(f"    Key {uid} found in local database only. Marking as INACTIVE.")
            if dry_run:
                logging.info(f"    [DRY RUN] Would mark key {uid} as INACTIVE.")
                continue
            
            with db_lock:
                local_keys[uid]['state'] = 'INACTIVE'
                local_keys[uid]['key_details']['last_updated_timestamp_utc'] = datetime.now(timezone.utc).isoformat()
        
        return gemini_key_exists

    except google_exceptions.PermissionDenied:
        logging.warning(f"  Permission denied to list keys for project {project_id}. Skipping sync.")
        return False
    except google_exceptions.GoogleAPICallError as err:
        logging.error(f"  An API error occurred while syncing keys for project {project_id}: {err}")
        return False

def process_project_for_action(project, creds, action, dry_run, db_lock, account_entry):
    """Processes a single project for the given action in a thread-safe manner."""
    project_id = project.project_id
    logging.info(f"- Starting to process project: {project_id} ({project.display_name})")

    if action == 'create':
        gemini_key_exists = sync_project_keys(project, creds, dry_run, db_lock, account_entry)
        if gemini_key_exists:
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


def generate_random_string(length=10):
    """Generates a random alphanumeric string of a given length."""
    letters_and_digits = string.ascii_lowercase + string.digits
    return ''.join(random.choice(letters_and_digits) for i in range(length))


def wait_for_project_ready(project_id, creds, timeout_seconds=300, initial_delay=5):
    """Waits for a newly created project to become fully active."""
    logging.info(f"  Waiting for project {project_id} to become fully active...")
    resource_manager = resourcemanager_v3.ProjectsClient(credentials=creds)
    start_time = time.time()
    delay = initial_delay

    while time.time() - start_time < timeout_seconds:
        try:
            resource_manager.get_project(name=f"projects/{project_id}")
            logging.info(f"  Project {project_id} is now active.")
            return True
        except google_exceptions.NotFound:
            logging.info(f"  Project {project_id} not found yet. Retrying in {delay} seconds...")
        except google_exceptions.PermissionDenied:
            logging.info(f"  Project {project_id} not accessible yet. Retrying in {delay} seconds...")
        except google_exceptions.GoogleAPICallError as e:
            logging.warning(f"  An API error occurred while waiting for project {project_id}: {e}. Retrying in {delay} seconds...")

        time.sleep(delay)
        delay = min(delay * 2, 30)

    logging.error(f"  Timed out waiting for project {project_id} to become active after {timeout_seconds} seconds.")
    return False


def create_projects_if_needed(projects, creds, dry_run=False):
    """Creates new projects if the account has fewer than 12 projects."""
    existing_project_count = len(projects)
    logging.info(f"Found {existing_project_count} existing projects.")
    newly_created_projects = []

    if existing_project_count >= 12:
        logging.info("Account already has 12 or more projects. No new projects will be created.")
        return newly_created_projects

    for i in range(existing_project_count, 12):
        project_number = str(i + 1).zfill(2)
        random_string = generate_random_string()
        project_id = f"project{project_number}-{random_string}"
        display_name = f"Project{project_number}"
        
        logging.info(f"Attempting to create project: ID='{project_id}', Name='{display_name}'")

        if dry_run:
            logging.info(f"[DRY RUN] Would create project '{display_name}' with ID '{project_id}'.")
            continue

        try:
            resource_manager = resourcemanager_v3.ProjectsClient(credentials=creds)
            project_to_create = resourcemanager_v3.Project(
                project_id=project_id,
                display_name=display_name
            )
            operation = resource_manager.create_project(project=project_to_create)
            logging.info(f"Waiting for project creation operation for '{display_name}' to complete...")
            created_project = operation.result()
            logging.info(f"Successfully created project '{display_name}'.")

            if wait_for_project_ready(project_id, creds):
                newly_created_projects.append(created_project)
            else:
                logging.error(f"Could not confirm project '{display_name}' ({project_id}) became active. It will be skipped.")

        except Exception as e:
            logging.error(f"Failed to create project '{display_name}': {e}")
    
    return newly_created_projects


def process_account(email, creds, action, api_keys_data, dry_run=False, max_workers=5):
    """Processes a single account for the given action."""
    logging.info(f"--- Processing account: {email} for action: {action} ---")
    if dry_run:
        logging.info("*** DRY RUN MODE ENABLED ***")

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

        if action == 'create':
            new_projects = create_projects_if_needed(projects, creds, dry_run)
            projects.extend(new_projects)

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

def get_and_refresh_credentials(email, max_retries=3, retry_delay=5):
    """Tries to load and refresh credentials for an email with retries. Does not start interactive flow."""
    token_file = os.path.join(CREDENTIALS_DIR, f"{email}.json")
    creds = None
    if os.path.exists(token_file):
        try:
            creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        except (ValueError, json.JSONDecodeError):
            logging.warning(f"Could not decode token file for {email}. Re-authentication will be required.")
            return None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        for attempt in range(max_retries):
            try:
                logging.info(f"Refreshing credentials for {email} (attempt {attempt + 1}/{max_retries})...")
                creds.refresh(google.auth.transport.requests.Request())
                with open(token_file, "w") as token:
                    token.write(creds.to_json())
                logging.info(f"Successfully refreshed credentials for {email}.")
                return creds
            except Exception as e:
                logging.warning(f"Failed to refresh credentials for {email} on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
        
        logging.error(f"Failed to refresh credentials for {email} after {max_retries} attempts.")
        return None
    
    return None

def run_interactive_auth(email, max_retries=3, retry_delay=5):
    """Runs the interactive OAuth2 flow for a given email with retries."""
    for attempt in range(max_retries):
        try:
            logging.info(f"Please authenticate with: {email} (attempt {attempt + 1}/{max_retries})")
            flow = InstalledAppFlow.from_client_secrets_file(
                CLIENT_SECRETS_FILE, SCOPES
            )
            creds = flow.run_local_server(port=0, login_hint=email)
            token_file = os.path.join(CREDENTIALS_DIR, f"{email}.json")
            with open(token_file, "w") as token:
                token.write(creds.to_json())
            return creds
        except Exception as e:
            logging.error(f"An unexpected error occurred during authentication for {email} on attempt {attempt + 1}: {e}")
            if attempt < max_retries - 1:
                logging.info(f"Retrying authentication in {retry_delay} seconds...")
                time.sleep(retry_delay)

    logging.error(f"Failed to authenticate {email} after {max_retries} attempts.")
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