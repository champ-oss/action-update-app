#!/usr/bin/env python3
# Description: Update files in a GitHub repository using GitHub App auth (no GitHub API).
import os
import subprocess
import time
from pathlib import Path
import json

import jwt
import requests
from git import Repo, GitCommandError
from tenacity import retry, wait_fixed, stop_after_attempt


def create_github_jwt(app_id: str, pem_path: str) -> str:
    """Create a short-lived GitHub App JWT."""
    now = int(time.time())
    payload = {"iat": now, "exp": now + 600, "iss": app_id}
    with open(pem_path, "r") as f:
        private_key = f.read()
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_github_access_token(app_id: str, installation_id: str, pem_path: str) -> str:
    """Request an installation access token for the GitHub App."""
    jwt_token = create_github_jwt(app_id, pem_path)
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
    """Clone the repository and configure Git identity."""
    print(f"Cloning branch '{branch}' from {repo_url} into {destination}")
    repo = Repo.clone_from(repo_url, destination, branch=branch)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "github-actions[bot]")
        cw.set_value("user", "email", "github-actions[bot]@users.noreply.github.com")
    return repo


def git_pull_repo(repo: Repo, branch: str):
    """Pull latest changes from remote branch."""
    origin = repo.remotes.origin
    print(f"Pulling latest changes from {branch}...")
    origin.fetch()
    try:
        origin.pull(branch, rebase=True)
    except GitCommandError as e:
        print(f"Pull failed (continuing): {e}")


def find_replace_file_pattern(search_string: str, replace_value: str, file_path: Path, suffix: str = '"'):
    """Find and replace text in the file using sed (known working version)."""
    print(f"Running sed on {file_path} to replace '{search_string}' → '{replace_value}'")
    subprocess.run(
        [
            "sed",
            "-i",
            "-e",
            f"s/{search_string}:.*/{search_string}:{replace_value}{suffix}/g",
            str(file_path),
        ],
        check=True,
    )


def git_commit_and_push(repo: Repo, branch: str, commit_message: str, token: str, repo_owner: str, repo_name: str):
    """Commit and push local changes using the GitHub App token."""
    if not repo.is_dirty(untracked_files=True):
        print("No changes detected. Skipping commit and push.")
        return

    repo.git.add(A=True)
    repo.index.commit(commit_message)
    print(f"Committed changes: {commit_message}")

    push_url = f"https://x-access-token:{token}@github.com/{repo_owner}/{repo_name}.git"
    origin = repo.remotes.origin
    origin.set_url(push_url)

    for i in range(5):
        try:
            print(f"Attempt {i+1}: pushing changes...")
            origin.push(refspec=f"{branch}:{branch}")
            print("Push successful!")
            return
        except GitCommandError as e:
            print(f"Push failed: {e}. Retrying with pull --rebase...")
            try:
                origin.pull(branch, rebase=True)
            except Exception as pe:
                print(f"Pull before retry failed: {pe}")
            time.sleep(5)

    raise Exception("ERROR: could not push to GitHub after 5 attempts")


@retry(wait=wait_fixed(4), stop=stop_after_attempt(10))
def main():
    app_id = os.environ["GITHUB_APP_ID"]
    installation_id = os.environ["GITHUB_INSTALLATION_ID"]
    private_key = os.environ["GITHUB_APP_PRIVATE_KEY"]
    repo_owner_target, _ = os.environ["GITHUB_REPOSITORY"].split("/")
    repo_name_target = os.environ["GITHUB_REPO_TARGET"]
    branch_name = os.environ.get("BRANCH", "main")

    directory = os.environ.get("DIRECTORY", ".update")
    directory_path = os.environ.get("DIRECTORY_PATH", "")
    file_path_list = json.loads(os.environ["FILE_PATH_LIST"])
    suffix = os.environ.get("SUFFIX", '"')
    search_key = os.environ.get("SEARCH_KEY", f"{os.environ['GITHUB_REPOSITORY'].split('/')[-1]}:")
    replace_value = os.environ.get("REPLACE_VALUE", os.environ.get("GITHUB_SHA"))

    if suffix == "off":
        suffix = ""

    # Write private key
    updated_private_key = private_key.replace("\\n", "\n").strip('"')
    with open("private.pem", "w") as f:
        f.write(updated_private_key)

    # Get token and clone
    access_token = get_github_access_token(app_id, installation_id, "private.pem")
    repo_url = f"https://x-access-token:{access_token}@github.com/{repo_owner_target}/{repo_name_target}.git"

    if os.path.exists(directory):
        os.system(f"rm -rf {directory}")
    repo = git_clone_repo(repo_url, directory, branch_name)
    git_pull_repo(repo, branch_name)

    # Update all listed files
    full_path = Path(directory) / directory_path if directory_path else Path(directory)
    for relative_file_path in file_path_list:
        target_file = full_path / relative_file_path
        find_replace_file_pattern(search_key, replace_value, target_file, suffix)

    # Commit and push
    git_commit_and_push(repo, branch_name, f"{search_key}{replace_value}", access_token, repo_owner_target, repo_name_target)


if __name__ == "__main__":
    main()
