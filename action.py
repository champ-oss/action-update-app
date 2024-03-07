#!/usr/bin/env python3
# Description: This action is used to update the file in the github repository.
import json
import subprocess
import time
import github.Auth
import jwt
import requests
from pathlib import Path
from git import Repo
import os
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


@retry(wait=wait_fixed(3), stop=stop_after_attempt(5))
def github_ref_edit(repo: github, branch_name: str, commit_sha: str) -> None:
    """
    Update the branch reference to the commit sha.

    :param repo: Repository
    :param branch_name: Branch name
    :param commit_sha: Commit sha
    """
    ref = repo.get_git_ref(f'heads/{branch_name}')
    ref.edit(commit_sha)


def main():
    app_id = os.environ.get('GITHUB_APP_ID')
    installation_id = os.environ.get('GITHUB_INSTALLATION_ID')
    private_key = os.environ.get('GITHUB_APP_PRIVATE_KEY')
    branch_name = os.environ.get('BRANCH', 'main')
    repo_owner_target = os.environ.get('GITHUB_OWNER_TARGET')
    search_string = os.environ.get('GITHUB_REPOSITORY').split('/')[1]
    repo_name_target = os.environ.get('GITHUB_REPO_TARGET')
    git_local_directory = os.environ.get('GIT_LOCAL_DIRECTORY', search_string)
    file_path_list = json.loads(os.environ['FILE_PATTERN_LIST'])
    updated_private_key = private_key.replace('\\n', '\n').strip('"')
    suffix = os.environ.get('SUFFIX', '"')
    replace_value = os.environ.get('GITHUB_SHA')
    # write private key to file
    with open('private.pem', 'w') as file:
        file.write(updated_private_key)
    # Get access token
    access_token = get_github_access_token(app_id, installation_id, 'private.pem')
    # Clone repo
    repo_url = f'https://x-access-token:{access_token}@github.com/{repo_owner_target}/{repo_name_target}.git'
    print(f'Cloning repo: {repo_url} to {git_local_directory}')
    destination_name = f'./{git_local_directory}'
    git_clone_repo(repo_url, destination_name, branch_name)
    # Update file
    github_client = github.Github(access_token)
    repo = github_client.get_repo(f'{repo_owner_target}/{repo_name_target}')
    element_list = []
    for file_pattern_path in file_path_list:
        updated_path = Path(destination_name) / file_pattern_path
        print(f'Updating path: {updated_path}')
        find_replace_file_pattern(search_string, replace_value, updated_path, suffix)
        data = updated_path.read_text()
        blob = repo.create_git_blob(data, 'utf-8')
        element = github.InputGitTreeElement(path=file_pattern_path, mode='100644', type='blob', sha=blob.sha)
        element_list.append(element)
    # update multiple files in same commit
    head_sha = repo.get_branch(branch_name).commit.sha
    branch_sha = repo.get_branch(branch_name).commit.sha
    base_tree = repo.get_git_tree(head_sha)
    new_tree = repo.create_git_tree(element_list, base_tree)
    parent = repo.get_git_commit(sha=branch_sha)
    commit = repo.create_git_commit("update commit sha using app bot", new_tree, [parent])
    github_ref_edit(repo, branch_name, commit.sha)


main()
