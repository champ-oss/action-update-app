#!/usr/bin/env python3
"""
GitHub Action: Update Docker image SHAs in files using a GitHub App
"""

import os
import json
import shutil
import subprocess
import time
from pathlib import Path
from git import Repo, GitCommandError
import jwt
import requests
from tenacity import retry, wait_fixed, stop_after_attempt

# -------------------------------
# GitHub App Auth
# -------------------------------
def create_github_jwt(app_id: str, private_key: str) -> str:
    now = int(time.time())
    payload = {"iat": now, "exp": now + 600, "iss": app_id}
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_github_access_token(app_id: str, installation_id: str, private_key: str) -> str:
    jwt_token = create_github_jwt(app_id, private_key)
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {"Authorization": f"Bearer {jwt_token}", "Accept": "application/vnd.github+json"}
    resp = requests.post(url, headers=headers)
    resp.raise_for_status()
    return resp.json()["token"]

# -------------------------------
# Sed-based SHA update
# -------------------------------
def find_replace_file_pattern(search_string: str, replace_string: str, file_path: str, suffix: str = '"') -> None:
    """
    Use sed to replace everything after search_string: with replace_string+suffix
    """
    subprocess.run(
        [
            "sed", "-i", "-e", f"s/{search_string}:.*/{search_string}:{replace_string}{suffix}/g", file_path
        ],
        check=True
    )

# -------------------------------
# Git commit & push
# -------------------------------
def commit_and_push(repo_path: Path, file_path: Path, commit_message: str, branch_name: str = "main"):
    repo = Repo(repo_path)
    repo.git.checkout(branch_name)

    try:
        repo.git.pull("--rebase")
        repo.index.add([str(file_path)])
        repo.index.commit(commit_message)
        repo.remote().push()
    except GitCommandError as e:
        if "CONFLICT" in str(e) or "rebase" in str(e):
            print(f"[ERROR] Rebase conflict detected: {e}")
            try:
                repo.git.rebase("--abort")
            except GitCommandError:
                pass
            print(f"[INFO] Cleaning local repo due to conflict: {repo_path}")
            shutil.rmtree(repo_path)
            raise RuntimeError(f"Rebase failed and repo cleaned: {e}")
        else:
            raise

# -------------------------------
# Main workflow
# -------------------------------
@retry(wait=wait_fixed(4), stop=stop_after_attempt(10))
def main():
    # --- Environment variables ---
    app_id = os.environ["GITHUB_APP_ID"]
    installation_id = os.environ["GITHUB_INSTALLATION_ID"]
    private_key = os.environ["GITHUB_APP_PRIVATE_KEY"].replace("\\n", "\n")
    branch_name = os.environ.get("BRANCH", "main")
    repo_owner = os.environ["GITHUB_REPOSITORY"].split("/")[0]
    repo_name = os.environ["GITHUB_REPO_TARGET"]
    git_local_dir = Path(os.environ.get("GIT_LOCAL_DIRECTORY", repo_name))
    file_path_list = json.loads(os.environ["FILE_PATH_LIST"])
    search_key = os.environ.get("SEARCH_KEY", repo_name)
    replace_value = os.environ.get("REPLACE_VALUE", os.environ.get("GITHUB_SHA"))
    suffix = os.environ.get("SUFFIX", '"')

    # --- Clean local repo ---
    if git_local_dir.exists():
        shutil.rmtree(git_local_dir)

    # --- Clone repo using GitHub App token ---
    access_token = get_github_access_token(app_id, installation_id, private_key)
    repo_url = f"https://x-access-token:{access_token}@github.com/{repo_owner}/{repo_name}.git"
    print(f"[INFO] Cloning repo: {repo_url} -> {git_local_dir}")
    Repo.clone_from(repo_url, git_local_dir, branch=branch_name)

    # --- Debug: list all files in repo ---
    all_files = [str(p.relative_to(git_local_dir)) for p in git_local_dir.rglob("*") if p.is_file()]
    print(f"[DEBUG] Files in repo after clone: {all_files}")

    # --- Update files ---
    for file_rel_path in file_path_list:
        file_path = git_local_dir / file_rel_path
        if not file_path.exists():
            print(f"[WARNING] File not found, skipping: {file_path}")
            continue

        # Use sed to update SHA
        find_replace_file_pattern(search_key, replace_value, str(file_path), suffix)

        # Commit & push changes
        commit_msg = f"{search_key}:{replace_value}"
        try:
            commit_and_push(git_local_dir, file_path, commit_msg, branch_name)
            print(f"[INFO] Updated & pushed: {file_rel_path}")
        except RuntimeError as e:
            print(f"[ERROR] Failed to push {file_rel_path}: {e}")

if __name__ == "__main__":
    main()
