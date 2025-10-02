#!/usr/bin/env python3
"""
GitHub Action: Find & replace in files, commit, and push to branch.
"""

import os
import re
import time
import shutil
from pathlib import Path
import json

import jwt
import requests
from git import Repo, GitCommandError
from tenacity import retry, wait_fixed, stop_after_attempt


def create_github_jwt(app_id: str, private_key: str) -> str:
    """Create GitHub JWT"""
    time_now = int(time.time())
    payload = {
        'iat': time_now,
        'exp': time_now + 600,  # 10 minutes
        'iss': app_id
    }
    token = jwt.encode(payload, private_key, algorithm='RS256')
    return token


def get_github_access_token(app_id: str, installation_id: str, private_key: str) -> str:
    """Get GitHub App access token"""
    jwt_token = create_github_jwt(app_id, private_key)
    url = f"https://api.github.com/app/installations/{installation_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt_token}",
        "Accept": "application/vnd.github+json"
    }
    response = requests.post(url, headers=headers)
    response.raise_for_status()
    return response.json()['token']


def find_replace_file_pattern(prefix: str, new_sha: str, file_path: str, suffix: str) -> bool:
    updated = False
    with open(file_path, "r") as f:
        content = f.read()

    # regex: capture prefix and swap only the SHA part
    pattern = rf'({re.escape(prefix)}:)[a-f0-9]+'
    replacement = rf'\1{new_sha}'
    new_content = re.sub(pattern, replacement, content)

    if new_content != content:
        with open(file_path, "w") as f:
            f.write(new_content)
        updated = True

    return updated


def update_file_git(repo_path: str, file_path: Path, commit_message: str, branch_name: str = "main") -> None:
    """
    Commit and push a file using GitPython, safely rebasing first.
    If a rebase conflict occurs, abort rebase and clean the repo directory.
    """
    repo = Repo(repo_path)
    repo.git.checkout(branch_name)

    try:
        # Pull with rebase
        repo.git.pull('--rebase')
        # Stage file and commit
        repo.index.add([str(file_path)])
        repo.index.commit(commit_message)
        repo.remote().push()
    except GitCommandError as e:
        # Detect rebase conflict
        if 'CONFLICT' in str(e) or 'rebase' in str(e):
            print(f"Rebase conflict detected: {e}")
            try:
                repo.git.rebase('--abort')
            except GitCommandError:
                pass
            # Clean local repo to allow retry
            print(f"Cleaning local repo due to conflict: {repo_path}")
            shutil.rmtree(repo_path)
            raise RuntimeError(f"Rebase failed and local repo cleaned: {e}")
        else:
            raise


@retry(wait=wait_fixed(4), stop=stop_after_attempt(10))
def main():
    # Environment variables
    app_id = os.environ['GITHUB_APP_ID']
    installation_id = os.environ['GITHUB_INSTALLATION_ID']
    private_key = os.environ['GITHUB_APP_PRIVATE_KEY'].replace('\\n', '\n')
    branch_name = os.environ.get('BRANCH', 'main')
    repo_owner = os.environ['GITHUB_REPOSITORY'].split('/')[0]
    repo_name = os.environ['GITHUB_REPO_TARGET']
    git_local_dir = Path(os.environ.get('GIT_LOCAL_DIRECTORY', repo_name))
    search_key = os.environ.get('SEARCH_KEY', repo_name)
    suffix = os.environ.get('SUFFIX', '"')
    gh_sha = os.environ.get('GITHUB_SHA')
    replace_value = os.environ.get('REPLACE_VALUE', gh_sha)
    file_path_list = json.loads(os.environ['FILE_PATH_LIST'])

    # Clean local directory
    if git_local_dir.exists():
        shutil.rmtree(git_local_dir)

    # Clone repo
    access_token = get_github_access_token(app_id, installation_id, private_key)
    repo_url = f"https://x-access-token:{access_token}@github.com/{repo_owner}/{repo_name}.git"
    print(f"Cloning repo to {git_local_dir}")
    Repo.clone_from(repo_url, git_local_dir, branch=branch_name)

    # Update files
    for file_pattern in file_path_list:
        file_path = git_local_dir / file_pattern
        if not file_path.exists():
            print(f"File not found: {file_path}, skipping")
            continue

        updated = find_replace_file_pattern(search_key, replace_value, file_path, suffix)
        if updated:
            commit_message = f"Update {search_key}-{gh_sha}"
            update_file_git(str(git_local_dir), file_path, commit_message, branch_name)
            print(f"Updated & pushed: {file_pattern}")
        else:
            print(f"No changes for: {file_pattern}")


if __name__ == "__main__":
    main()

