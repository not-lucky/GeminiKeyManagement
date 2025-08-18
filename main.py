import os
import sys
import argparse
import re
import google.auth
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.cloud import resourcemanager_v3, service_usage_v1, api_keys_v2
from google.api_core import exceptions as google_exceptions
from google.auth.transport import requests

# --- CONFIGURATION ---
CREDENTIALS_DIR = "credentials"
EMAILS_FILE = "emails.txt"
CLIENT_SECRETS_FILE = "credentials.json"
# ---------------------

SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
]

def load_emails_from_file(filename):
    """Loads a list of emails from a text file, ignoring comments."""
    if not os.path.exists(filename):
        print(f"Error: Email file not found at '{filename}'")
        print("Please create it and add one email address per line.")
        return []
    with open(filename, "r") as f:
        # Ignore empty lines and lines starting with #
        return [line.strip() for line in f if line.strip() and not line.startswith("#")]

def load_existing_api_keys(email):
    """Loads API keys from the user's key file."""
    filename = f"{email}.keys.txt"
    keys = set()
    if os.path.exists(filename):
        with open(filename, "r") as f:
            content = f.read()
            # Find all occurrences of "Key: <key_string>"
            found_keys = re.findall(r"Key:\s*(.+)", content)
            keys.update(found_keys)
    return keys

def main():
    """Main function to orchestrate API key creation or deletion."""
    parser = argparse.ArgumentParser(description="Manage Gemini API keys in Google Cloud projects.")
    parser.add_argument("action", choices=['create', 'delete'], help="The action to perform: 'create' or 'delete' API keys.")
    parser.add_argument("--email", help="Specify a single email address to process. Required for 'delete' action. If not provided for 'create', emails will be read from emails.txt.")
    args = parser.parse_args()

    if not os.path.exists(CLIENT_SECRETS_FILE):
        print(f"Error: OAuth client secrets file not found at '{CLIENT_SECRETS_FILE}'")
        print("Please follow the setup instructions in README.md to create it.")
        sys.exit(1)

    if not os.path.exists(CREDENTIALS_DIR):
        os.makedirs(CREDENTIALS_DIR)

    emails_to_process = []
    if args.email:
        emails_to_process.append(args.email)
    elif args.action == 'delete':
        print("Error: The 'delete' action requires the --email argument.")
        sys.exit(1)
    else: # action is 'create' and no email provided
        emails_to_process = load_emails_from_file(EMAILS_FILE)
        if not emails_to_process:
            print("No emails found in emails.txt. Exiting.")
            sys.exit(1)

    for email in emails_to_process:
        process_account(email, args.action)

def process_account(email, action):
    """Processes a single account for the given action."""
    print(f"--- Processing account: {email} for action: {action} ---")
    creds = get_credentials_for_email(email)
    if not creds:
        print(f"Could not get credentials for {email}. Skipping.")
        return

    # Load existing keys if we are creating
    existing_keys = set()
    if action == 'create':
        existing_keys = load_existing_api_keys(email)
        if existing_keys:
            print(f"Loaded {len(existing_keys)} existing keys from file.")

    try:
        resource_manager = resourcemanager_v3.ProjectsClient(credentials=creds)
        projects = list(resource_manager.search_projects())

        if not projects:
            print(f"No projects found for {email}.")
            return

        print(f"Found {len(projects)} projects. Processing...")
        for project in projects:
            project_id = project.project_id
            print(f"- Project: {project_id}")

            if action == 'create':
                # Check if a key with the specific display name already exists
                if project_has_gemini_key(project_id, creds):
                    print("  'Gemini API Key' already exists in this project. Skipping creation.")
                    continue

                if enable_api(project_id, creds):
                    key = create_api_key(project_id, creds)
                    if key:
                        save_api_key(email, project_id, key.key_string)
            elif action == 'delete':
                delete_api_keys(project_id, creds)

    except google_exceptions.PermissionDenied as err:
        print(f"Permission denied for account {email}. Check IAM roles.")
        print(f"  Error: {err}")
    except google_exceptions.GoogleAPICallError as err:
        print(f"An API error occurred while processing account {email}: {err}")

def project_has_gemini_key(project_id, credentials):
    """Checks if a project already has a key named 'Gemini API Key'."""
    try:
        api_keys_client = api_keys_v2.ApiKeysClient(credentials=credentials)
        parent = f"projects/{project_id}/locations/global"
        keys = api_keys_client.list_keys(parent=parent)
        for key in keys:
            if key.display_name == "Gemini API Key":
                return True
        return False
    except google_exceptions.GoogleAPICallError as err:
        print(f"  Could not list keys in project {project_id}. Assuming no key exists. Error: {err}")
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
                print("Refreshing credentials...")
                creds.refresh(google.auth.transport.requests.Request())
            else:
                print(f"Please authenticate with: {email}")
                flow = InstalledAppFlow.from_client_secrets_file(
                    CLIENT_SECRETS_FILE, SCOPES
                )
                # Use login_hint to pre-fill the email address
                creds = flow.run_local_server(port=0, login_hint=email)
            with open(token_file, "w") as token:
                token.write(creds.to_json())
        return creds
    except Exception as e:
        print(f"An unexpected error occurred during authentication: {e}")
        return None


def enable_api(project_id, credentials):
    """Enables the Generative Language API for a project."""
    try:
        service_usage_client = service_usage_v1.ServiceUsageClient(credentials=credentials)
        service_name = "generativelanguage.googleapis.com"
        request = service_usage_v1.EnableServiceRequest(
            name=f"projects/{project_id}/services/{service_name}"
        )
        operation = service_usage_client.enable_service(request=request)
        print("Waiting for API enablement to complete...")
        operation.result()  # Wait for the operation to complete
        print(f"Enabled Generative Language API for project {project_id}")
        return True
    except google_exceptions.PermissionDenied as err:
        print(f"Permission denied to enable API for project {project_id}. Skipping.")
        return False
    except google_exceptions.GoogleAPICallError as err:
        # Check if the error is that the API is already enabled
        if "already been enabled" in str(err):
            print(f"Generative Language API is already enabled for project {project_id}")
            return True
        print(f"Error enabling API for project {project_id}: {err}")
        return False

def create_api_key(project_id, credentials):
    """Creates a new, restricted API key."""
    try:
        api_keys_client = api_keys_v2.ApiKeysClient(credentials=credentials)

        # Define the API target for restrictions
        api_target = api_keys_v2.ApiTarget(service="generativelanguage.googleapis.com")

        # Define the key with restrictions
        key = api_keys_v2.Key(
            display_name="Gemini API Key",
            restrictions=api_keys_v2.Restrictions(
                api_targets=[api_target]
            ),
        )

        request = api_keys_v2.CreateKeyRequest(
            parent=f"projects/{project_id}/locations/global",
            key=key,
        )
        print("Creating API key...")
        operation = api_keys_client.create_key(request=request)
        result = operation.result()  # Wait for the operation to complete
        print(f"Successfully created restricted API key for project {project_id}")
        return result
    except google_exceptions.PermissionDenied as err:
        print(f"Permission denied to create API key for project {project_id}. Skipping.")
        return None
    except google_exceptions.GoogleAPICallError as err:
        print(f"Error creating API key for project {project_id}: {err}")
        return None

def delete_api_keys(project_id, credentials):
    """Deletes all API keys with the display name 'Gemini API Key'."""
    try:
        api_keys_client = api_keys_v2.ApiKeysClient(credentials=credentials)
        parent = f"projects/{project_id}/locations/global"

        keys = api_keys_client.list_keys(parent=parent)
        keys_to_delete = [key for key in keys if key.display_name == "Gemini API Key"]

        if not keys_to_delete:
            print(f"  No 'Gemini API Key' found to delete.")
            return

        print(f"  Found {len(keys_to_delete)} key(s) with display name 'Gemini API Key'. Deleting...")
        for key in keys_to_delete:
            try:
                request = api_keys_v2.DeleteKeyRequest(name=key.name)
                operation = api_keys_client.delete_key(request=request)
                operation.result()  # Wait for completion
                print(f"  Successfully deleted key: {key.uid}")
            except google_exceptions.GoogleAPICallError as err:
                print(f"  Error deleting key {key.uid}: {err}")

    except google_exceptions.PermissionDenied as err:
        print(f"  Permission denied to list or delete API keys for project {project_id}. Skipping.")
    except google_exceptions.GoogleAPICallError as err:
        print(f"  An API error occurred while deleting keys for project {project_id}: {err}")


def save_api_key(email, project_id, api_key):
    """Saves the API key to a file, including the project ID."""
    filename = f"{email}.keys.txt"
    with open(filename, "a") as f:
        f.write(f"Project: {project_id}\nKey: {api_key}\n\n")
    print(f"Saved API key to {filename}")

if __name__ == "__main__":
    main()