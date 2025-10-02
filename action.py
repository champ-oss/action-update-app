#!/usr/bin/env python3
# Description: This action updates files in a GitHub repo with rebase support on conflicts.

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


def find_replace_file_pattern(search_string: str, replace_string: str, file_path: Path, suffix: str):
    subprocess.call(
        ['sed', '-i', '-e', f's/{search_string}:.*/{search_string}:{replace_string}{suffix}/g', str(file_path)]
    )


def update_file(repo, branch_name: str, file_path: str, search_string: str, gh_sha: str, content: str = None) -> Any | None:
    sha = repo.get_contents(file_path, ref=branch_name).sha
    try:
        response = repo.update_file(
            path=file_path,
            message=f'updated {search_string}-{gh_sha}',
            content=content,
            sha=sha,
            branch=branch_name
        )
        return response is not None
    except Exception as e:
        print(f'Error updating {file_path}: {e}')
        return None


def git_commit_and_rebase(repo_dir: str, branch_name: str, commit_message: str):
    repo = Repo(repo_dir)
    try:
        repo.git.add(all=True)
        repo.index.commit(commit_message)
        print(f'Committed changes: {commit_message}')
        try:
            repo.git.pull('--rebase', 'origin', branch_name)
            print('Rebase successful')
        except GitCommandError as e:
            print(f'Rebase failed: {e}')
            raise e
        repo.git.push('origin', branch_name)
        print('Push successful')
    except GitCommandError as e:
        print(f'Git operation failed: {e}')
        raise e


@retry(wait=wait_fixed(5), stop=stop_after_attempt(5))
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

    updated_private_key = private_key.replace('\\n', '\n').strip('"')
    with open('private.pem', 'w') as f:
        f.write(updated_private_key)

    access_token = get_github_access_token(app_id, installation_id, 'private.pem')
    repo_url = f'https://x-access-token:{access_token}@github.com/{repo_owner_target}/{repo_name_target}.git'

    # Clean workspace
    if os.path.exists(git_local_directory):
        subprocess.call(['rm', '-rf', git_local_directory])

    print(f'Cloning repo {repo_url} to {git_local_directory}')
    Repo.clone_from(repo_url, git_local_directory, branch=branch_name)

    github_client = github.Github(access_token)
    repo = github_client.get_repo(f'{repo_owner_target}/{repo_name_target}')

    # Update files
    for file_pattern in file_path_list:
        updated_file_path = Path(git_local_directory) / file_pattern
        find_replace_file_pattern(search_string, replace_value, updated_file_path, suffix)
        if updated_file_path.exists():
            with open(updated_file_path, 'r') as f:
                content = f.read()
            update_file_status = update_file(repo, branch_name, file_pattern, search_string, gh_sha, content)
            if update_file_status:
                print(f'File updated successfully: {file_pattern}')
            else:
                raise Exception(f'Failed to update file: {file_pattern}')

    # Commit, rebase, and push
    commit_message = f'Update {search_string}-{gh_sha}'
    git_commit_and_rebase(git_local_directory, branch_name, commit_message)


if __name__ == '__main__':
    main()
