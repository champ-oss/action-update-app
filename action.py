#!/usr/bin/env python3
# Description: This action is used to update the file in the github repository.
import json
import subprocess
import time
import github.Auth
import jwt
import requests
from pathlib import Path
import os

from git import Repo, Remote
from github import Repository
from tenacity import retry, wait_fixed, stop_after_attempt


def create_github_jwt(app_id: str, pem: str) -> str:
    """
    Create GitHub JWT.

    :param app_id: GitHub App's identifier
    :param pem: Path to the private
    :return: GitHub JWT
    """
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
    """
    Get GitHub App access token.

    :param app_id: GitHub App's identifier
    :param installation_id: GitHub App's installation identifier
    :param pem: Path to the private
    :return: GitHub App access token
    """
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


def git_pull(repo_path: Repo) -> None:
    """
    Pull the changes from the remote repository.

    :param repo_path: Repository path
    """
    origin: Remote = repo_path.remotes.origin
    origin.pull()


def git_clone_repo(repo_url: str, destination_name: str, branch_name: str) -> Repo:
    """
    Clone the repository.

    :param repo_url: Repository URL
    :param destination_name: Destination name
    :param branch_name: Branch name
    """
    repo = Repo.clone_from(repo_url, destination_name, branch=branch_name)
    return repo


def find_replace_file_pattern(search_string: str, replace_string: str, file_pattern, suffix: str) -> None:
    """
    Find and replace pattern in file.

    :param suffix: default is double quotes to end the line.
    :param file_pattern: file_pattern
    :param search_string: search_string
    :param replace_string: replace_string to update
    """
    subprocess.call(
        [
            'sed', '-i', '-e', f's/{search_string}:.*/{search_string}:{replace_string}{suffix}/g', file_pattern
        ]
    )


@retry(wait=wait_fixed(4), stop=stop_after_attempt(15))
def update_file(repo: Repository, branch_name: str, file_path: str, search_string: str, gh_sha: str,
                get_repo: Repo, content: str = None) -> str:
    """
    Update a file in the repo.

    :param file_path: Path to file
    :param repo: Repo to add file
    :param get_repo: Repo to clone
    :param search_string: search_string for message
    :param content: Content of the file
    :param gh_sha: gh sha for message.
    :param branch_name: Name of branch
    :return: SHA of the new commit
    """
    git_pull(get_repo)
    sha = repo.get_contents(file_path, ref=branch_name).sha
    try:
        response = repo.update_file(path=file_path, message=f'updated {search_string}-{gh_sha}',
                                    content=content, sha=sha, branch=branch_name)
    except Exception as e:
        print(f'Error occurred while updating the file: {e}')
        raise
    return response['commit'].sha


def main():
    app_id = os.environ.get('GITHUB_APP_ID')
    installation_id = os.environ.get('GITHUB_INSTALLATION_ID')
    private_key = os.environ.get('GITHUB_APP_PRIVATE_KEY')
    branch_name = os.environ.get('BRANCH', 'main')
    repo_owner_target = os.environ.get('GITHUB_REPOSITORY').split('/')[0]
    search_string = os.environ.get('SEARCH_KEY', os.environ.get('GITHUB_REPOSITORY').split('/')[1])
    repo_name_target = os.environ.get('GITHUB_REPO_TARGET')
    git_local_directory = os.environ.get('GIT_LOCAL_DIRECTORY', repo_name_target)
    file_path_list = json.loads(os.environ['FILE_PATH_LIST'])
    updated_private_key = private_key.replace('\\n', '\n').strip('"')
    suffix = os.environ.get('SUFFIX', '"')
    gh_sha = os.environ.get('GITHUB_SHA')
    replace_value = os.environ.get('REPLACE_VALUE', gh_sha)
    # write private key to file
    with open('private.pem', 'w') as file:
        file.write(updated_private_key)
    # Get access token
    access_token = get_github_access_token(app_id, installation_id, 'private.pem')
    # Clone repo
    repo_url = f'https://x-access-token:{access_token}@github.com/{repo_owner_target}/{repo_name_target}.git'
    print(f'Cloning repo: {repo_url} to {git_local_directory}')
    get_repo = git_clone_repo(repo_url, git_local_directory, branch_name)
    # Update file
    github_client = github.Github(access_token)
    repo = github_client.get_repo(f'{repo_owner_target}/{repo_name_target}')
    for file_pattern in file_path_list:
        updated_file_path = Path(git_local_directory) / file_pattern
        find_replace_file_pattern(search_string, replace_value, updated_file_path, suffix)
        if updated_file_path.exists():
            with open(updated_file_path, 'r') as file:
                content = file.read()
            update_file(repo, branch_name, file_pattern, search_string, gh_sha, git_local_directory, get_repo, content)
        else:
            print(f'File {file_pattern} does not exist in the repository.')

    print('File updated successfully.')


main()
