#!/usr/bin/env python3
# Description: Update files in a GitHub repository via GitHub App authentication.
import os
import subprocess
import time
from pathlib import Path
import json

import jwt
import requests
from git import Repo, GitCommandError
from tenacity import retry, wait_fixed, stop_after_attempt


def create_github_jwt(app_id: str, pem: str) -> str:
    now = int(time.time())
    payload = {"iat": now, "exp": now + 600, "iss": app_id}
    with open(pem, "r") as f:
        private_key = f.read()
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_github_access_token(app_id: str, installation_id: str, pem: str) -> str:
    jwt_token = create_github_jwt(app_id, pem)
    res = requests.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    res.raise_for_status()
    return res.json()["token"]


def git_clone_repo(repo_url: str, destination: str, branch: str) -> Repo:
    print(f"Cloning branch '{branch}' from {repo_url} into {destination}")
    repo = Repo.clone_from(repo_url, destination, branch=branch)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "github-actions[bot]")
        cw.set_value("user", "email", "github-actions[bot]@users.noreply.github.com")
    return repo


def git_pull_repo(repo: Repo, branch: str):
    origin = repo.remotes.origin
    print(f"Pulling latest changes from {branch}...")
    origin.fetch()
    try:
        origin.pull(branch, rebase=True)
        print("Repository up-to-date.")
    except GitCommandError as e:
        print(f"Pull failed: {e}")


def find_replace_file_pattern(search_string: str, replace_value: str, file_path: Path, suffix: str = '"'):
    subprocess.run(
        [
            "sed",
            "-i",
            f"s/{search_string}.*/{search_string}{replace_value}{suffix}/g",
            str(file_path),
        ],
        check=True,
    )
    print(f"Updated {file_path} for {search_string} → {replace_value}")


def git_commit_and_push(repo: Repo, branch: str, commit_message: str, token: str, repo_owner: str, repo_name: str):
    """
    Commit changes and push using GitHub App token via subprocess.
    Retries up to 5 times on non-fast-forward errors.
    """
    # Stage all changes
    repo.git.add(A=True)

    # Commit
    try:
        repo.index.commit(commit_message)
        print(f"Committed changes: {commit_message}")
    except Exception as e:
        print(f"No changes to commit: {e}")
        return

    push_url = f"https://x-access-token:{token}@github.com/{repo_owner}/{repo_name}.git"
    repo_dir = repo.working_tree_dir

    for attempt in range(5):
        try:
            print(f"Attempt {attempt + 1}: pushing changes to {branch}...")
            subprocess.run(
                ["git", "push", push_url, f"{branch}:{branch}"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            print("✅ Push successful!")
            return
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.strip() if e.stderr else ""
            print(f"Push failed: {stderr}")

            if "non-fast-forward" in stderr or "fetch first" in stderr:
                print("Detected non-fast-forward. Pulling latest changes and retrying...")
                subprocess.run(["git", "pull", "--rebase", push_url, branch], cwd=repo_dir, check=True)
                time.sleep(2)
            else:
                print("Unknown push failure. Retrying...")
                time.sleep(2)

    raise Exception("ERROR: Could not push changes after 5 attempts")



@retry(wait=wait_fixed(4), stop=stop_after_attempt(10))
def main():
    app_id = os.environ.get("GITHUB_APP_ID")
    installation_id = os.environ.get("GITHUB_INSTALLATION_ID")
    private_key = os.environ.get("GITHUB_APP_PRIVATE_KEY")
    repo_owner_target, _ = os.environ.get("GITHUB_REPOSITORY").split("/")
    repo_name_target = os.environ.get("GITHUB_REPO_TARGET")
    branch_name = os.environ.get("BRANCH", "main")

    directory = os.environ.get("DIRECTORY", ".update")
    directory_path = os.environ.get("DIRECTORY_PATH", "")
    file_path_list = json.loads(os.environ['FILE_PATH_LIST'])
    suffix = os.environ.get("SUFFIX", '"')
    search_key = os.environ.get("SEARCH_KEY", f"{os.environ.get('GITHUB_REPOSITORY').split('/')[-1]}:")
    replace_value = os.environ.get("REPLACE_VALUE", os.environ.get("GITHUB_SHA"))

    if suffix == "off":
        suffix = ""

    updated_private_key = private_key.replace("\\n", "\n").strip('"')
    with open("private.pem", "w") as f:
        f.write(updated_private_key)

    access_token = get_github_access_token(app_id, installation_id, "private.pem")
    repo_url = f"https://x-access-token:{access_token}@github.com/{repo_owner_target}/{repo_name_target}.git"

    # Clean and clone
    if os.path.exists(directory):
        os.system(f"rm -rf {directory}")
    repo = git_clone_repo(repo_url, directory, branch_name)
    git_pull_repo(repo, branch_name)

    # Update files
    full_path = Path(directory) / directory_path if directory_path else Path(directory)
    target_file = full_path / file_path_list[0] if len(file_path_list) == 1 else None
    find_replace_file_pattern(search_key, replace_value, target_file, suffix)

    # Commit and push changes using approved token
    git_commit_and_push(repo, branch_name, f"{search_key}{replace_value}", access_token, repo_owner_target, repo_name_target)


if __name__ == "__main__":
    main()

