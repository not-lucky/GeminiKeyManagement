import os
import sys
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
    """Loads a list of emails from a text file."""
    if not os.path.exists(filename):
        print(f"Error: Email file not found at '{filename}'")
        print("Please create it and add one email address per line.")
        return []
    with open(filename, "r") as f:
        return [line.strip() for line in f if line.strip()]

def main():
    """Main function to orchestrate API key creation."""
    if not os.path.exists(CLIENT_SECRETS_FILE):
        print(f"Error: OAuth client secrets file not found at '{CLIENT_SECRETS_FILE}'")
        print("Please follow the setup instructions in README.md to create it.")
        sys.exit(1)

    if not os.path.exists(CREDENTIALS_DIR):
        os.makedirs(CREDENTIALS_DIR)

    emails = load_emails_from_file(EMAILS_FILE)
    if not emails:
        sys.exit(1)

    for email in emails:
        print(f"--- Processing account: {email} ---")
        creds = get_credentials_for_email(email)
        if not creds:
            print(f"Could not get credentials for {email}. Skipping.")
            continue

        try:
            resource_manager = resourcemanager_v3.ProjectsClient(credentials=creds)
            projects = list(resource_manager.search_projects()) # Convert iterator to list

            if not projects:
                print(f"No projects found for {email}.")
                continue

            print(f"Found {len(projects)} projects. Processing...")
            for project in projects:
                project_id = project.project_id
                print(f"- Project: {project_id}")
                if enable_api(project_id, creds):
                    key = create_api_key(project_id, creds)
                    if key:
                        save_api_key(email, project_id, key.key_string)

        except google_exceptions.PermissionDenied as err:
            print(f"Permission denied for account {email}. Check IAM roles.")
            print(f"  Error: {err}")
        except google_exceptions.GoogleAPICallError as err:
            print(f"An API error occurred while processing account {email}: {err}")

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

def save_api_key(email, project_id, api_key):
    """Saves the API key to a file, including the project ID."""
    filename = f"{email}.keys.txt"
    with open(filename, "a") as f:
        f.write(f"Project: {project_id}\nKey: {api_key}\n\n")
    print(f"Saved API key to {filename}")

if __name__ == "__main__":
    main()