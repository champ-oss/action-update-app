#!/usr/bin/env python3
"""
GitHub Action: Update Docker image SHAs in files using a GitHub App
"""

import os
import json
import time
import shutil
from pathlib import Path
from git import Repo, GitCommandError
import jwt
import requests
from tenacity import retry, wait_fixed, stop_after_attempt


# -------------------------------
# GitHub App Auth
# -------------------------------
def create_github_jwt(app_id: str, private_key: str) -> str:
    """Create GitHub JWT for App authentication"""
    now = int(time.time())
    payload = {
        "iat": now,
        "exp": now + 600,  # 10 min
        "iss": app_id
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_github_access_token(app_id: str, installation_id: str, private_key: str) -> str:
    """Get GitHub App installation token"""
    jwt_token = create_github_jwt(app_id, private_key)
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {"Authorization": f"Bearer {jwt_token}", "Accept": "application/vnd.github+json"}
    resp = requests.post(url, headers=headers)
    resp.raise_for_status()
    return resp.json()["token"]


# -------------------------------
# File update function
# -------------------------------
def update_file_literal(file_path: Path, search_key: str, replace_value: str, suffix: str = '"') -> bool:
    """
    Replace everything after search_key in a line with replace_value + suffix (mimics bash sed)
    Returns True if file was changed
    """
    content = file_path.read_text()
    lines = content.splitlines()
    new_lines = []
    changed = False

    for line in lines:
        if search_key in line:
            idx = line.find(search_key) + len(search_key)
            new_line = line[:idx] + replace_value + (suffix if suffix else "")
            if new_line != line:
                changed = True
            new_lines.append(new_line)
        else:
            new_lines.append(line)

    if changed:
        file_path.write_text("\n".join(new_lines) + "\n")
    return changed


# -------------------------------
# Git commit & push
# -------------------------------
def commit_and_push(repo_path: Path, file_path: Path, commit_message: str, branch_name: str = "main"):
    """
    Commit a file and push changes safely, aborting rebase on conflicts
    """
    repo = Repo(repo_path)
    repo.git.checkout(branch_name)

    try:
        repo.git.pull("--rebase")
        repo.index.add([str(file_path)])
        repo.index.commit(commit_message)
        repo.remote().push()
    except GitCommandError as e:
        if "CONFLICT" in str(e) or "rebase" in str(e):
            print(f"Rebase conflict detected: {e}")
            try:
                repo.git.rebase("--abort")
            except GitCommandError:
                pass
            print(f"Cleaning local repo due to conflict: {repo_path}")
            shutil.rmtree(repo_path)
            raise RuntimeError(f"Rebase failed and repo cleaned: {e}")
        else:
            raise


# -------------------------------
# Main workflow
# -------------------------------
@retry(wait=wait_fixed(4), stop=stop_after_attempt(10))
def main():
    # --- Environment Variables ---
    app_id = os.environ["GITHUB_APP_ID"]
    installation_id = os.environ["GITHUB_INSTALLATION_ID"]
    private_key = os.environ["GITHUB_APP_PRIVATE_KEY"].replace("\\n", "\n")
    branch_name = os.environ.get("BRANCH", "main")
    repo_owner = os.environ["GITHUB_REPOSITORY"].split("/")[0]
    repo_name = os.environ["GITHUB_REPO_TARGET"]
    git_local_dir = Path(os.environ.get("GIT_LOCAL_DIRECTORY", repo_name))
    file_path_list = json.loads(os.environ["FILE_PATH_LIST"])
    search_key = os.environ.get("SEARCH_KEY", f"{repo_name}:")
    replace_value = os.environ.get("REPLACE_VALUE", os.environ.get("GITHUB_SHA"))
    suffix = os.environ.get("SUFFIX", "")

    # --- Clean local repo ---
    if git_local_dir.exists():
        shutil.rmtree(git_local_dir)

    # --- Clone repo ---
    access_token = get_github_access_token(app_id, installation_id, private_key)
    repo_url = f"https://x-access-token:{access_token}@github.com/{repo_owner}/{repo_name}.git"
    print(f"Cloning repo: {repo_url} -> {git_local_dir}")
    Repo.clone_from(repo_url, git_local_dir, branch=branch_name)

    # --- Update files ---
    for file_rel_path in file_path_list:
        file_path = git_local_dir / file_rel_path
        if not file_path.exists():
            print(f"File not found, skipping: {file_path}")
            continue

        updated = update_file_literal(file_path, search_key, replace_value, suffix)
        if updated:
            commit_msg = f"{search_key}{replace_value}"
            commit_and_push(git_local_dir, file_path, commit_msg, branch_name)
            print(f"Updated & pushed: {file_rel_path}")
        else:
            print(f"No changes for: {file_rel_path}")


if __name__ == "__main__":
    main()
