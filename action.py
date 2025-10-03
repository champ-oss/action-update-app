#!/usr/bin/env python3
# Description: Update file in GitHub repo using API only, exact key match

import json
import time
import os
from typing import Any
import jwt
import requests
from github import Github, Repository
from tenacity import retry, wait_fixed, stop_after_attempt


def create_github_jwt(app_id: str, pem: str) -> str:
    """Create GitHub JWT."""
    time_now = int(time.time())
    payload = {'iat': time_now, 'exp': time_now + 600, 'iss': app_id}
    jwt_token = jwt.encode(payload, pem, algorithm='RS256')
    return jwt_token

def get_github_access_token(app_id: str, installation_id: str, pem: str) -> str:
    """Get GitHub App access token."""
    create_jwt = create_github_jwt(app_id, pem)
    response = requests.post(
        f'https://api.github.com/app/installations/{installation_id}/access_tokens',
        headers={'Authorization': f'Bearer {create_jwt}', 'Accept': 'application/vnd.github+json'}
    )
    response.raise_for_status()
    return response.json()['token']


def update_file(repo: Repository, branch_name: str, file_path: str, gh_sha: str, updated_content: str) -> bool:
    """Update file via API, fetching latest SHA before update."""
    try:
        file_info = repo.get_contents(file_path, ref=branch_name)
        sha = file_info.sha
        repo.update_file(
            path=file_path,
            message=f'Update {file_path}-{gh_sha}',
            content=updated_content,
            sha=sha,
            branch=branch_name
        )
        return True
    except Exception as e:
        print(f"Error updating {file_path}: {e}")
        return False


@retry(wait=wait_fixed(4), stop=stop_after_attempt(15))
def main():
    app_id = os.environ.get('GITHUB_APP_ID')
    installation_id = os.environ.get('GITHUB_INSTALLATION_ID')
    private_key = os.environ.get('GITHUB_APP_PRIVATE_KEY').replace('\\n', '\n').strip('"')
    branch_name = os.environ.get('BRANCH', 'main')
    repo_owner_target = os.environ.get('GITHUB_REPOSITORY').split('/')[0]
    repo_name_target = os.environ.get('GITHUB_REPO_TARGET')
    search_string = os.environ.get('SEARCH_KEY', os.environ.get('GITHUB_REPOSITORY').split('/')[1])
    file_path_list = json.loads(os.environ['FILE_PATH_LIST'])
    gh_sha = os.environ.get('GITHUB_SHA')
    replace_value = os.environ.get('REPLACE_VALUE', gh_sha)
    suffix = os.environ.get('SUFFIX', '"')

    access_token = get_github_access_token(app_id, installation_id, private_key)
    github_client = Github(access_token)
    repo = github_client.get_repo(f'{repo_owner_target}/{repo_name_target}')

    for file_path in file_path_list:
        # fetch current file content
        file_info = repo.get_contents(file_path, ref=branch_name)
        content_lines = file_info.decoded_content.decode().splitlines()

        # modify lines in-memory with exact key match
        updated_lines = [
            f"{search_string}:{replace_value}{suffix}"
            if line.split(':', 1)[0].strip() == search_string
            else line
            for line in content_lines
        ]
        updated_content = "\n".join(updated_lines)

        # update via API
        success = update_file(repo, branch_name, file_path, gh_sha, updated_content)
        if success:
            print(f"File updated successfully: {file_path}")
        else:
            raise Exception(f"Failed to update file: {file_path}")


main()
