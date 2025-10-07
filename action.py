#!/usr/bin/env python3
# Description: This action updates files in a GitHub repository with per-file git pull.

import json
import subprocess
import time
from typing import Any
import github
import jwt
import requests
from pathlib import Path
import os
from git import Repo
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
    return response.json()['token']


def git_clone_repo(repo_url: str, destination_name: str, branch_name: str) -> Repo:
    repo = Repo.clone_from(repo_url, destination=destination_name, branch=branch_name)
    return repo


def git_pull_file(repo_dir: str, token: str, repo_owner: str, repo_name: str, branch_name: str):
    """
    Pull latest changes for the repo before updating a file.
    """
    pull_url = f"https://x-access-token:{token}@github.com/{repo_owner}/{repo_name}.git"
    try:
        subprocess.run(
            ["git", "pull", "--rebase", pull_url, branch_name],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True
        )
        print("✅ Git pull successful.")
    except subprocess.CalledProcessError as e:
        print(f"⚠️ Git pull failed: {e.stderr.strip() if e.stderr else e}")


def find_replace_file_pattern(search_string: str, replace_string: str, file_pattern, suffix: str) -> None:
    subprocess.call(
        ['sed', '-i', '-e', f's/{search_string}:.*/{search_string}:{replace_string}{suffix}/g', file_pattern]
    )


def update_file(repo, branch_name: str, file_path: str,
                search_string: str, gh_sha: str, content: str = None) -> Any | None:
    try:
        sha = repo.get_contents(file_path, ref=branch_name).sha
        response = repo.update_file(
            path=file_path,
            message=f'updated {search_string}-{gh_sha}',
            content=content,
            sha=sha,
            branch=branch_name
        )
        return response is not None
    except Exception as e:
        print(f'❌ Error while updating {file_path}: {e}')
        return None


@retry(wait=wait_fixed(4), stop=stop_after_attempt(15))
def main():
    app_id = os.environ.get('GITHUB_APP_ID')
    installation_id = os.environ.get('GITHUB_INSTALLATION_ID')
    private_key = os.environ.get('GITHUB_APP_PRIVATE_KEY')
    branch_name = os.environ.get('BRANCH', 'main')
    repo_owner_target = os.environ.get('GITHUB_REPOSITORY').split('/')[0]
    repo_name_target = os.environ.get('GITHUB_REPO_TARGET')
    git_local_directory = os.environ.get('GIT_LOCAL_DIRECTORY', repo_name_target)
    os.system(f'rm -rf {git_local_directory} || true')
    file_path_list = json.loads(os.environ['FILE_PATH_LIST'])
    updated_private_key = private_key.replace('\\n', '\n').strip('"')
    suffix = os.environ.get('SUFFIX', '"')
    search_string = os.environ.get('SEARCH_KEY', os.environ.get('GITHUB_REPOSITORY').split('/')[1])
    gh_sha = os.environ.get('GITHUB_SHA')
    replace_value = os.environ.get('REPLACE_VALUE', gh_sha)

    with open('private.pem', 'w') as file:
        file.write(updated_private_key)

    access_token = get_github_access_token(app_id, installation_id, 'private.pem')
    repo_url = f'https://x-access-token:{access_token}@github.com/{repo_owner_target}/{repo_name_target}.git'
    print(f'Cloning repo: {repo_url} to {git_local_directory}')
    repo = git_clone_repo(repo_url, git_local_directory, branch_name)

    github_client = github.Github(access_token)
    gh_repo = github_client.get_repo(f'{repo_owner_target}/{repo_name_target}')

    for file_pattern in file_path_list:
        updated_file_path = Path(git_local_directory) / file_pattern

        # ✅ Git pull right before updating this file
        git_pull_file(git_local_directory, access_token, repo_owner_target, repo_name_target, branch_name)

        find_replace_file_pattern(search_string, replace_value, updated_file_path, suffix)

        if updated_file_path.exists():
            with open(updated_file_path, 'r') as file:
                content = file.read()
            if update_file(gh_repo, branch_name, file_pattern, search_string, gh_sha, content):
                print(f'✅ File updated successfully: {file_pattern}')
            else:
                os.system(f'rm -rf {git_local_directory} || true')
                raise Exception(f'Error occurred while updating the file: {file_pattern}')


if __name__ == "__main__":
    main()
