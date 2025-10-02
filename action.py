#!/usr/bin/env python3
# Description: Update files in GitHub repo with automatic rebase retry on conflicts

import json
import subprocess
import time
import os
from pathlib import Path
from typing import Any

import jwt
import requests
import github
from git import Repo, GitCommandError
from tenacity import retry, wait_fixed, stop_after_attempt


def create_github_jwt(app_id: str, pem: str) -> str:
    time_now = int(time.time())
    payload = {'iat': time_now, 'exp': time_now + 600, 'iss': app_id}
    with open(pem, 'r') as file:
        private_key = file.read()
    return jwt.encode(payload, private_key, algorithm='RS256')


def get_github_access_token(app_id: str, installation_id: str, pem: str) -> str:
    jwt_token = create_github_jwt(app_id, pem)
    response = requests.post(
        f'https://api.github.com/app/installations/{installation_id}/access_tokens',
        headers={'Authorization': f'Bearer {jwt_token}',
                 'Accept': 'application/vnd.github+json'}
    )
    response.raise_for_status()
    return response.json()['token']


def find_replace_file_pattern(search_string: str, replace_string: str, file_path: Path, suffix: str):
    subprocess.call(
        ['sed', '-i', '-e', f's/{search_string}:.*/{search_string}:{replace_string}{suffix}/g', str(file_path)]
    )


def git_push_with_rebase(repo: Repo, branch_name: str, max_attempts: int = 5, delay: int = 5):
    """
    Try to push changes, rebase if push fails, retry up to max_attempts.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            repo.git.push('origin', branch_name)
            print("Push successful")
            return
        except GitCommandError as e:
            print(f"Push failed on attempt {attempt}: {e}")
            try:
                repo.git.pull('--rebase', 'origin', branch_name)
                print(f"Rebase successful, retrying push in {delay}s...")
                time.sleep(delay)
            except GitCommandError as rebase_err:
                print(f"Rebase failed: {rebase_err}")
                raise rebase_err
    raise Exception(f"Failed to push after {max_attempts} attempts")


@retry(wait=wait_fixed(5), stop=stop_after_attempt(3))
def main():
    app_id = os.environ.get('GITHUB_APP_ID')
    installation_id = os.environ.get('GITHUB_INSTALLATION_ID')
    private_key = os.environ.get('GITHUB_APP_PRIVATE_KEY')
    branch_name = os.environ.get('BRANCH', 'main')
    repo_owner_target = os.environ.get('GITHUB_REPOSITORY').split('/')[0]
    repo_name_target = os.environ.get('GITHUB_REPO_TARGET')
    git_local_directory = os.environ.get('GIT_LOCAL_DIRECTORY', repo_name_target)
    search_string = os.environ.get('SEARCH_KEY', os.environ.get('GITHUB_REPOSITORY').split('/')[1])
    file_path_list = json.loads(os.environ['FILE_PATH_LIST'])
    gh_sha = os.environ.get('GITHUB_SHA')
    replace_value = os.environ.get('REPLACE_VALUE', gh_sha)
    suffix = os.environ.get('SUFFIX', '"')

    # Prepare private key
    updated_private_key = private_key.replace('\\n', '\n').strip('"')
    with open('private.pem', 'w') as f:
        f.write(updated_private_key)

    # GitHub access
    access_token = get_github_access_token(app_id, installation_id, 'private.pem')
    repo_url = f'https://x-access-token:{access_token}@github.com/{repo_owner_target}/{repo_name_target}.git'

    # Clean workspace
    if os.path.exists(git_local_directory):
        subprocess.call(['rm', '-rf', git_local_directory])

    print(f'Cloning repo {repo_url} to {git_local_directory}')
    repo = Repo.clone_from(repo_url, git_local_directory, branch=branch_name)

    # Update files
    for file_pattern in file_path_list:
        file_path = Path(git_local_directory) / file_pattern
        find_replace_file_pattern(search_string, replace_value, file_path, suffix)

    # Commit changes
    repo.git.add(all=True)
    commit_message = f"Update {search_string}-{gh_sha}"
    repo.index.commit(commit_message)
    print(f"Committed changes: {commit_message}")

    # Push with rebase retry
    git_push_with_rebase(repo, branch_name)


if __name__ == '__main__':
    main()
