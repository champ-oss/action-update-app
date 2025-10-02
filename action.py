#!/usr/bin/env python3
import json
import os
import time
from pathlib import Path

import github
import jwt
import requests
from tenacity import retry, wait_fixed, stop_after_attempt

# -----------------------------
# GitHub App Authentication
# -----------------------------
def create_github_jwt(app_id: str, pem_path: str) -> str:
    import time
    with open(pem_path, 'r') as f:
        private_key = f.read()
    payload = {
        'iat': int(time.time()),
        'exp': int(time.time()) + 600,  # 10 minutes
        'iss': app_id
    }
    return jwt.encode(payload, private_key, algorithm='RS256')


def get_github_app_installation_token(app_id: str, installation_id: str, pem_path: str) -> str:
    jwt_token = create_github_jwt(app_id, pem_path)
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {"Authorization": f"Bearer {jwt_token}", "Accept": "application/vnd.github+json"}
    resp = requests.post(url, headers=headers)
    resp.raise_for_status()
    return resp.json()["token"]

# -----------------------------
# File update with SHA retry
# -----------------------------
def update_file_with_retry(repo, branch, file_path, new_content, max_attempts=5):
    """
    Update a file safely via GitHub API using SHA retry.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            file_info = repo.get_contents(file_path, ref=branch)
            sha = file_info.sha
            repo.update_file(
                path=file_path,
                message=f"Update {file_path}",
                content=new_content,
                sha=sha,
                branch=branch
            )
            print(f"[SUCCESS] File updated: {file_path}")
            return True
        except github.GithubException as e:
            if e.status == 409:  # Conflict: SHA mismatch
                print(f"[RETRY] SHA conflict for {file_path}, attempt {attempt}/{max_attempts}")
                time.sleep(2)
                continue
            else:
                raise
    raise Exception(f"Failed to update {file_path} after {max_attempts} attempts")

# -----------------------------
# Main action
# -----------------------------
def main():
    app_id = os.environ.get("GITHUB_APP_ID")
    installation_id = os.environ.get("GITHUB_INSTALLATION_ID")
    private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY")
    branch_name = os.environ.get("BRANCH", "main")
    repo_name_target = os.environ.get("GITHUB_REPO_TARGET")
    repo_owner_target = os.environ.get("GITHUB_REPOSITORY").split("/")[0]
    file_path_list = json.loads(os.environ["FILE_PATH_LIST"])
    replace_value = os.environ.get("REPLACE_VALUE", "")

    # Write private key to file for JWT
    with open("private.pem", "w") as f:
        f.write(private_key.replace("\\n", "\n").strip('"'))

    token = get_github_app_installation_token(app_id, installation_id, "private.pem")
    gh = github.Github(token)
    repo = gh.get_repo(f"{repo_owner_target}/{repo_name_target}")

    for file_pattern in file_path_list:
        file_path = Path(file_pattern)
        file_info = repo.get_contents(file_pattern, ref=branch_name)
        content = file_info.decoded_content.decode()
        updated_content = content.replace(file_pattern.split("/")[-1], replace_value)
        update_file_with_retry(repo, branch_name, file_pattern, updated_content)

if __name__ == "__main__":
    main()
