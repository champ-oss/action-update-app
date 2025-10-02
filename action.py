#!/usr/bin/env python3
"""
GitHub Action: Update Docker image SHAs in files and push changes using GitHub App
"""

import os
import json
import time
import shutil
import re
from pathlib import Path

import jwt
import requests
from git import Repo, GitCommandError
from tenacity import retry, wait_fixed, stop_after_attempt


# -------------------------------
# GitHub App Auth
# -------------------------------
def create_github_jwt(app_id: str, private_key: str) -> str:
    """Create GitHub JWT for App authentication"""
    time_now = int(time.time())
    payload = {
        'iat': time_now,
        'exp': time_now + 600,  # 10 minutes
        'iss': app_id
    }
    return jwt.encode(payload, private_key, algorithm='RS256')


def get_github_access_token(app_id: str, installation_id: str, private_key: str) -> str:
    """Get installation access token"""
    jwt_token = create_github_jwt(app_id, private_key)
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json"
    }
    resp = requests.post(url, headers=headers)
    resp.raise_for_status()
    return resp.json()['token']


# -------------------------------
# File update functions
# -------------------------------
def find_replace_git_sha(file_path: Path, image_prefix: str, new_sha: str) -> bool:
    """
    Replace the SHA after a Docker image prefix in a file.
    Returns True if file was changed.
    """
    content = file_path.read_text()

    escaped_prefix = re.escape(image_prefix)
    pattern = rf'({escaped_prefix}:)[a-f0-9]+'
    replacement = rf'\1{new_sha}'

    new_content = re.sub(pattern, replacement, content)
    if new_content != content:
        file_path.write_text(new_content)
        return True
    return False


# -------------------------------
# Git update functions
# -------------------------------
def update_file_git(repo_path: str, file_path: Path, commit_message: str, branch_name: str = "main") -> None:
    """
    Commit and push a file using GitPython, safely rebasing first.
    Auto-abort and clean repo on rebase conflicts.
    """
    repo = Repo(repo_path)
    repo.git.checkout(branch_name)

    try:
        repo.git.pull('--rebase')
        repo.index.add([str(file_path)])
        repo.index.commit(commit_message)
        repo.remote().push()
    except GitCommandError as e:
        if 'CONFLICT' in str(e) or 'rebase' in str(e):
            print(f"Rebase conflict detected: {e}")
            try:
                repo.git.rebase('--abort')
            except GitCommandError:
                pass
            print(f"Cleaning local repo due to conflict: {repo_path}")
            shutil.rmtree(repo_path)
            raise RuntimeError(f"Rebase failed and local repo cleaned: {e}")
        else:
            raise


# -------------------------------
# Main workflow
# -------------------------------
@retry(wait=wait_fixed(4), stop=stop_after_attempt(10))
def main():
    # Read environment variables
    app_id = os.environ['GITHUB_APP_ID']
    installation_id = os.environ['GITHUB_INSTALLATION_ID']
    private_key = os.environ['GITHUB_APP_PRIVATE_KEY'].replace('\\n', '\n')
    branch_name = os.environ.get('BRANCH', 'main')
    repo_owner = os.environ['GITHUB_REPOSITORY'].split('/')[0]
    repo_name = os.environ['GITHUB_REPO_TARGET']
    git_local_dir = Path(os.environ.get('GIT_LOCAL_DIRECTORY', repo_name))
    file_path_list = json.loads(os.environ['FILE_PATH_LIST'])
    image_prefix = os.environ.get('SEARCH_KEY', repo_name)
    new_sha = os.environ.get('REPLACE_VALUE', os.environ.get('GITHUB_SHA'))

    # Clean local repo directory if exists
    if git_local_dir.exists():
        shutil.rmtree(git_local_dir)

    # Clone repository
    access_token = get_github_access_token(app_id, installation_id, private_key)
    repo_url = f"https://x-access-token:{access_token}@github.com/{repo_owner}/{repo_name}.git"
    print(f"Cloning repo to {git_local_dir}")
    Repo.clone_from(repo_url, git_local_dir, branch=branch_name)

    # Update files
    for file_rel_path in file_path_list:
        file_path = git_local_dir / file_rel_path
        if not file_path.exists():
            print(f"File not found: {file_path}, skipping")
            continue

        updated = find_replace_git_sha(file_path, image_prefix, new_sha)
        if updated:
            commit_message = f"Update {image_prefix}-{new_sha}"
            update_file_git(str(git_local_dir), file_path, commit_message, branch_name)
            print(f"Updated & pushed: {file_rel_path}")
        else:
            print(f"No changes for: {file_rel_path}")


if __name__ == "__main__":
    main()
