#!/usr/bin/env python3
# Description: Update files in a GitHub repository using minimal changes.

import json
import subprocess
import time
from pathlib import Path
import os

import jwt
import requests
from git import Repo, GitCommandError
from tenacity import retry, wait_fixed, stop_after_attempt


def create_github_jwt(app_id: str, pem: str) -> str:
    time_now = int(time.time())
    payload = {
        'iat': time_now,
        'exp': time_now + (10 * 60),
        'iss': app_id
    }
    with open(pem, 'r') as file:
        private_key = file.read()
    jwt_token = jwt.encode(payload, private_key, algorithm='RS256')
    return jwt_token


def get_github_access_token(app_id: str, installation_id: str, pem: str) -> str:
    create_jwt = create_github_jwt(app_id, pem)
    response = requests.post(
        f'https://api.github.com/app/installations/{installation_id}/access_tokens',
        headers={
            'Authorization': f'Bearer {create_jwt}',
            'Accept': 'application/vnd.github+json'
        }
    )
    response.raise_for_status()
    access_token = response.json()['token']
    return access_token


def git_clone_repo(repo_url: str, destination_name: str, branch_name: str) -> Repo:
    repo = Repo.clone_from(repo_url, destination_name, branch=branch_name)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "github-actions[bot]")
        cw.set_value("user", "email", "github-actions[bot]@users.noreply.github.com")
    return repo


def find_replace_file_pattern(search_string: str, replace_string: str, file_pattern, suffix: str) -> None:
    subprocess.call(
        [
            'sed', '-i', '-e', f's/{search_string}:.*/{search_string}:{replace_string}{suffix}/g', file_pattern
        ]
    )


def git_commit_and_push(repo: Repo, branch: str, commit_message: str, token: str, repo_owner: str, repo_name: str):
    if not repo.is_dirty(untracked_files=True):
        print("No changes detected. Skipping commit and push.")
        return

    repo.git.add(A=True)
    repo.index.commit(commit_message)
    print(f"Committed changes: {commit_message}")

    push_url = f"https://x-access-token:{token}@github.com/{repo_owner}/{repo_name}.git"
    origin = repo.remotes.origin
    origin.set_url(push_url)

    try:
        origin.push(refspec=f"{branch}:{branch}")
        print("Push successful!")
    except GitCommandError as e:
        print(f"Push failed: {e}")
        raise


@retry(wait=wait_fixed(4), stop=stop_after_attempt(15))
def main():
    app_id = os.environ.get('GITHUB_APP_ID')
    installation_id = os.environ.get('GITHUB_INSTALLATION_ID')
    private_key = os.environ.get('GITHUB_APP_PRIVATE_KEY')
    branch_name = os.environ.get('BRANCH', 'main')
    repo_owner_target = os.environ.get('GITHUB_REPOSITORY').split('/')[0]
    search_string = os.environ.get('SEARCH_KEY', os.environ.get('GITHUB_REPOSITORY').split('/')[1])
    repo_name_target = os.environ.get('GITHUB_REPO_TARGET')
    git_local_directory = os.environ.get('GIT_LOCAL_DIRECTORY', repo_name_target)
    os.system(f'rm -rf {git_local_directory} || true')
    file_path_list = json.loads(os.environ['FILE_PATH_LIST'])
    updated_private_key = private_key.replace('\\n', '\n').strip('"')
    suffix = os.environ.get('SUFFIX', '"')
    gh_sha = os.environ.get('GITHUB_SHA')
    replace_value = os.environ.get('REPLACE_VALUE', gh_sha)

    # write private key to file
    with open('private.pem', 'w') as file:
        file.write(updated_private_key)

    access_token = get_github_access_token(app_id, installation_id, 'private.pem')
    repo_url = f'https://x-access-token:{access_token}@github.com/{repo_owner_target}/{repo_name_target}.git'
    print(f'Cloning repo: {repo_url} to {git_local_directory}')
    repo = git_clone_repo(repo_url, git_local_directory, branch_name)

    # update files locally
    full_path = Path(git_local_directory)
    for file_pattern in file_path_list:
        updated_file_path = full_path / file_pattern
        find_replace_file_pattern(search_string, replace_value, updated_file_path, suffix)

    # commit and push changes
    git_commit_and_push(repo, branch_name, f"{search_string}:{replace_value}", access_token, repo_owner_target, repo_name_target)


if __name__ == "__main__":
    main()
