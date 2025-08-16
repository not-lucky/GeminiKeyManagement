import os
import time
import google.auth
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/service.management",
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

        resource_manager = build("cloudresourcemanager", "v1", credentials=creds)
        projects = resource_manager.projects().list().execute()

        print("Processing projects...")
        for project in projects.get("projects", []):
            project_id = project['projectId']
            print(f"- {project_id}")
            enable_api(project_id, creds)
            key = create_api_key(project_id, creds)
            if key:
                save_api_key(email, key["keyString"])

    except HttpError as err:
        print(err)


def get_user_email(credentials):
    try:
        userinfo = build("oauth2", "v2", credentials=credentials)
        return userinfo.userinfo().get().execute()["email"]
    except HttpError as err:
        print(f"Error getting user email: {err}")
        return None


def enable_api(project_id, credentials):
    try:
        service_usage = build("serviceusage", "v1", credentials=credentials)
        service_name = "generativelanguage.googleapis.com"
        service_usage.services().enable(
            name=f"projects/{project_id}/services/{service_name}"
        ).execute()
        print(f"Enabled Generative Language API for project {project_id}")
    except HttpError as err:
        print(f"Error enabling API for project {project_id}: {err}")


def create_api_key(project_id, credentials):
    try:
        service = build("apikeys", "v2", credentials=credentials)
        key = {
            "displayName": "Gemini API Key"
        }
        request = (
            service.projects()
            .locations()
            .keys()
            .create(parent=f"projects/{project_id}/locations/global", body=key)
        )
        operation = request.execute()
        op_name = operation["name"]

        op_service = service.operations()
        while True:
            op_request = op_service.get(name=op_name)
            op_response = op_request.execute()
            if op_response.get("done"):
                if op_response.get("error"):
                    raise Exception(f"GCP key creation failed: {op_response['error']}")
                print(f"Created API key for project {project_id}")
                return op_response["response"]
    except HttpError as err:
        print(f"Error creating API key for project {project_id}: {err}")
        return None


def save_api_key(email, api_key):
    with open(f"{email}.key", "a") as f:
        f.write(f"{api_key}\n")


if __name__ == "__main__":
    main()