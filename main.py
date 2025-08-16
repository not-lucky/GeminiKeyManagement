import os
import google.auth
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.cloud import resourcemanager_v3, service_usage_v1, api_keys_v2
from google.api_core import exceptions as google_exceptions
from google.oauth2 import id_token
from google.auth.transport import requests

SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid"
]

def main():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(google.auth.transport.requests.Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    try:
        email = get_user_email(creds)
        if not email:
            return

        resource_manager = resourcemanager_v3.ProjectsClient(credentials=creds)
        projects = resource_manager.search_projects()

        print("Processing projects...")
        for project in projects:
            project_id = project.project_id
            print(f"- {project_id}")
            enable_api(project_id, creds)
            key = create_api_key(project_id, creds)
            if key:
                save_api_key(email, key.key_string)

    except google_exceptions.GoogleAPICallError as err:
        print(err)


def get_user_email(credentials):
    try:
        request = requests.Request()
        id_info = id_token.verify_oauth2_token(
            credentials.id_token, request, clock_skew_in_seconds=3
        )
        return id_info["email"]
    except ValueError as err:
        print(f"Error getting user email: {err}")
        return None


def enable_api(project_id, credentials):
    try:
        service_usage_client = service_usage_v1.ServiceUsageClient(credentials=credentials)
        service_name = "generativelanguage.googleapis.com"
        request = service_usage_v1.EnableServiceRequest(
            name=f"projects/{project_id}/services/{service_name}"
        )
        operation = service_usage_client.enable_service(request=request)
        # Wait for the operation to complete
        operation.result()
        print(f"Enabled Generative Language API for project {project_id}")
    except google_exceptions.GoogleAPICallError as err:
        print(f"Error enabling API for project {project_id}: {err}")


def create_api_key(project_id, credentials):
    try:
        api_keys_client = api_keys_v2.ApiKeysClient(credentials=credentials)
        key = api_keys_v2.Key(
            display_name="Gemini API Key"
        )
        request = api_keys_v2.CreateKeyRequest(
            parent=f"projects/{project_id}/locations/global",
            key=key,
        )
        operation = api_keys_client.create_key(request=request)
        # Wait for the operation to complete
        result = operation.result()
        print(f"Created API key for project {project_id}")
        return result
    except google_exceptions.GoogleAPICallError as err:
        print(f"Error creating API key for project {project_id}: {err}")
        return None


def save_api_key(email, api_key):
    with open(f"{email}.key", "a") as f:
        f.write(f"{api_key}\n")


if __name__ == "__main__":
    main()