
# Gemini API Key Management

This script automates the creation and deletion of Gemini API keys across all Google Cloud projects accessible by one or more user accounts. It's designed to streamline the process of provisioning API keys for Google's Generative Language models (like Gemini), ensuring consistency and proper restrictions.

## Features

- **Automated Key Creation**: Iterates through all accessible Google Cloud projects and creates a new, restricted "Gemini API Key" if one doesn't already exist.
- **Automated Key Deletion**: Scans all projects and deletes any keys named "Gemini API Key".
- **Multi-Account Support**: Can be configured to run for multiple Google accounts.
- **OAuth 2.0 Authentication**: Securely authenticates with each Google account using OAuth 2.0, storing refresh tokens for subsequent runs.
- **Idempotent Creation**: The script will not create a new key in a project that already has a key named "Gemini API Key".
- **JSON Database**: Maintains a local JSON file (`api_keys_database.json`) as a record of all the keys it has created and their details.
- **Dry Run Mode**: Allows you to see what changes the script would make without actually creating or deleting any cloud resources.

## Prerequisites

- uv.
- Access to a Google Cloud Platform project where you can enable APIs and create OAuth 2.0 credentials.

## Setup

1.  **Clone the repository:**

    ```bash
    git clone https://github.com/not-lucky/GeminiKeyManagement.git
    cd gemini-key-management
    ```

2.  **Create and activate a virtual environment:**

    This project uses `uv` for package management.

    ```bash
    uv sync
    ```

3.  **Create OAuth 2.0 Credentials:**

    a.  Go to the [Google Cloud Console](https://console.cloud.google.com/).
    b.  Select a project or create a new one.
    c.  Navigate to **APIs & Services > Credentials**.
    d.  Click **+ CREATE CREDENTIALS** and select **OAuth client ID**.
    e.  Choose **Desktop app** as the application type.
    f.  Give it a name (e.g., "Gemini Key Manager CLI").
    g.  Click **Create**.
    h.  A window will pop up showing the client ID and secret. Click **DOWNLOAD JSON**.
    i.  Save this file in the root of the project directory and rename it to `credentials.json`.

    **Important**: This `credentials.json` file is sensitive. Ensure it is not committed to version control. The `.gitignore` file in this repository should already be configured to ignore it.

4.  **Prepare the list of emails:**

    Create a file named `emails.txt` in the root of the project. Add the email address of each Google account you want to process, one per line.

    Example `emails.txt`:

    ```
    # This is a comment, it will be ignored
    user1@example.com
    user2@example.com
    ```

## Usage

The script has two main actions: `create` and `delete`.

### Creating API Keys

To create Gemini API keys for all users listed in `emails.txt`:

```bash
uv run main.py create
```

The first time you run this for a particular user, a browser window will open, and you will be prompted to log in and grant permission. After successful authentication, an access token will be saved in the `credentials/` directory, and subsequent runs will not require manual intervention (unless the token expires or is revoked).

### Deleting API Keys

To delete all keys named "Gemini API Key" for a specific user:

```bash
uv run main.py delete --email user1@example.com
```

**Note**: The `--email` argument is required for the `delete` action for safety.

### Dry Run

To see what the script *would* do without making any actual changes to your Google Cloud resources, use the `--dry-run` flag.

```bash
uv run main.py create --dry-run
uv run main.py delete --email user1@example.com --dry-run
```

## Output

-   **Logs**: A detailed log file is created in the `logs/` directory for each run, named with a UTC timestamp (e.g., `gemini_key_management_2023-10-27T12-30-00.log`).
-   **Database**: The `api_keys_database.json` file is created or updated after each successful run. This file contains a structured record of the accounts processed, the projects found, and the API keys created by the script.

## How it Works

1.  **Authentication**: For each email, the script looks for a corresponding `[email].json` token file in the `credentials/` directory. If found and valid, it uses it. If not, it initiates the OAuth 2.0 flow.
2.  **Project Discovery**: It uses the Google Cloud Resource Manager API to find all projects the authenticated user has access to.
3.  **API Enablement**: For each project, it checks if the "Generative Language API" (`generativelanguage.googleapis.com`) is enabled. If not, it attempts to enable it.
4.  **Key Creation/Deletion**:
    -   **Create**: It checks if a key named "Gemini API Key" already exists. If not, it creates a new key using the API Keys API. The key is restricted to only be able to call the `generativellanguage.googleapis.com` service.
    -   **Delete**: It finds all keys with the display name "Gemini API Key" and deletes them.
5.  **Database Update**: The script records the details of any created keys in the `api_keys_database.json` file. When keys are deleted, they are removed from this database.
