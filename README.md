# Gemini Key Management

This script automates the process of creating and deleting Google Cloud API keys for the Generative Language API (Gemini) across multiple Google Cloud projects.

## Prerequisites

- Python 3.12 or higher
- A Google Cloud account and project

## Setup

1.  **Create OAuth Client ID credentials:**
    - Go to the [Google Cloud Console](https://console.cloud.google.com/apis/credentials).
    - Click on **Create Credentials** and select **OAuth client ID**.
    - Select **Desktop app** as the application type.
    - Give it a name and click **Create**.
    - Download the JSON file and save it as `credentials.json` in the root of this project.

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Create `emails.txt` file (for creating keys in bulk):**
    - Create a file named `emails.txt` in the root of the project.
    - Add the email addresses of the Google accounts you want to process, one email per line. You can add comments with `#`.

## Usage

Run the script from your terminal with a specified action (`create` or `delete`).

### Dry Run

You can simulate any action without making real changes by adding the `--dry-run` flag. This is useful for testing and verifying the script's behavior.

```bash
python main.py create --dry-run
python main.py delete --email your.email@example.com --dry-run
```

### Creating API Keys

-   **For all users in `emails.txt`:**
    ```bash
    python main.py create
    ```

-   **For a single user:**
    ```bash
    python main.py create --email your.email@example.com
    ```

The first time you run the script for a new email address, you will be prompted to authenticate with your Google account in your web browser. A token file will be saved in the `credentials` directory for future runs.

The script will then:
- Find all Google Cloud projects accessible by the authenticated user.
- Enable the `generativelanguage.googleapis.com` API for each project.
- Create a new API key named "Gemini API Key" with restrictions to the Generative Language API.
- Save the API key(s) to a central `api_keys_database.json` file.

### Deleting API Keys

To delete all API keys with the display name "Gemini API Key" for a specific user, the `--email` argument is required:

```bash
python main.py delete --email your.email@example.com
```

This will iterate through all projects accessible by that user and remove the matching keys.

