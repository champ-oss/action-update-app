#!/usr/bin/env python3
# Description: Hybrid Git + API updater using GitHub App authentication
import os
import subprocess
import time
import json
from pathlib import Path
import base64
import jwt
import requests
from git import Repo
from tenacity import retry, wait_fixed, stop_after_attempt


def create_github_jwt(app_id: str, pem: str) -> str:
    """Create a GitHub App JWT."""
    now = int(time.time())
    payload = {"iat": now, "exp": now + 600, "iss": app_id}
    with open(pem, "r") as f:
        private_key = f.read()
    return jwt.encode(payload, private_key, algorithm="RS256")


def get_github_access_token(app_id: str, installation_id: str, pem: str) -> str:
    """Exchange the App JWT for an installation access token."""
    jwt_token = create_github_jwt(app_id, pem)
    res = requests.post(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        headers={"Authorization": f"Bearer {jwt_token}", "Accept": "application/vnd.github+json"},
    )
    res.raise_for_status()
    return res.json()["token"]


def git_clone_repo(repo_url: str, destination: str, branch: str) -> Repo:
    """Clone a repository and configure user."""
    print(f"Cloning branch '{branch}' from {repo_url} into {destination}")
    repo = Repo.clone_from(repo_url, destination, branch=branch)
    with repo.config_writer() as cw:
        cw.set_value("user", "name", "github-actions[bot]")
        cw.set_value("user", "email", "github-actions[bot]@users.noreply.github.com")
    return repo


def find_replace_file_pattern(search_string: str, replace_value: str, file_path: Path, suffix: str = '"'):
    """Simple sed-based in-place replacement."""
    subprocess.run(
        ["sed", "-i", f"s/{search_string}.*/{search_string}{replace_value}{suffix}/g", str(file_path)],
        check=True,
    )
    print(f"Updated {file_path} for {search_string} → {replace_value}")


def git_commit_and_push_single_file(repo: Repo, branch: str, file_path: str, commit_message: str, token: str, repo_owner: str, repo_name: str):
    """Commit and push via Git, fallback to API if push fails."""
    repo.git.add(os.path.relpath(file_path, repo.working_tree_dir))
    try:
        repo.index.commit(commit_message)
        print(f"Committed changes to {file_path}")
    except Exception as e:
        print(f"No changes to commit: {e}")
        return

    push_url = f"https://x-access-token:{token}@github.com/{repo_owner}/{repo_name}.git"
    repo.remotes.origin.set_url(push_url)
    repo_dir = repo.working_tree_dir

    # Pull latest before push
    try:
        subprocess.run(["git", "pull", "--rebase", push_url, branch], cwd=repo_dir, check=True)
    except subprocess.CalledProcessError as e:
        print(f"Initial pull failed: {e.stderr if e.stderr else e}")

    # Try pushing via Git
    for attempt in range(3):
        try:
            subprocess.run(["git", "push", push_url, f"{branch}:{branch}"], cwd=repo_dir, check=True)
            print(f"✅ Push successful for {file_path}")
            return True
        except subprocess.CalledProcessError as e:
            stderr = e.stderr or ""
            print(f"Push failed (attempt {attempt+1}): {stderr}")
            if "non-fast-forward" in stderr:
                subprocess.run(["git", "pull", "--rebase", push_url, branch], cwd=repo_dir, check=True)
            time.sleep(3)

    print("⚠️ Falling back to API update for this file...")
    return False


def api_update_file(repo_owner, repo_name, branch, token, file_path, commit_message):
    """Update a single file using the GitHub API."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    api_url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/contents/{file_path}"

    # Get existing file SHA
    res = requests.get(f"{api_url}?ref={branch}", headers=headers)
    res.raise_for_status()
    sha = res.json()["sha"]

    with open(file_path, "rb") as f:
        content = base64.b64encode(f.read()).decode("utf-8")

    payload = {
        "message": commit_message,
        "content": content,
        "sha": sha,
        "branch": branch,
    }

    res = requests.put(api_url, headers=headers, data=json.dumps(payload))
    if res.status_code in (200, 201):
        print(f"✅ API updated {file_path} successfully.")
    else:
        print(f"❌ API update failed: {res.status_code} {res.text}")


@retry(wait=wait_fixed(4), stop=stop_after_attempt(5))
def main():
    app_id = os.environ["GITHUB_APP_ID"]
    installation_id = os.environ["GITHUB_INSTALLATION_ID"]
    private_key = os.environ["GITHUB_APP_PRIVATE_KEY"]
    repo_owner, _ = os.environ["GITHUB_REPOSITORY"].split("/")
    repo_name = os.environ["GITHUB_REPO_TARGET"]
    branch = os.environ.get("BRANCH", "main")
    file_path_list = json.loads(os.environ["FILE_PATH_LIST"])
    suffix = os.environ.get("SUFFIX", '"')
    search_key = os.environ.get("SEARCH_KEY", "version:")
    replace_value = os.environ.get("REPLACE_VALUE", os.environ.get("GITHUB_SHA"))

    directory = os.environ.get("DIRECTORY", ".update")
    updated_private_key = private_key.replace("\\n", "\n").strip('"')
    with open("private.pem", "w") as f:
        f.write(updated_private_key)

    token = get_github_access_token(app_id, installation_id, "private.pem")
    repo_url = f"https://x-access-token:{token}@github.com/{repo_owner}/{repo_name}.git"

    if os.path.exists(directory):
        os.system(f"rm -rf {directory}")
    repo = git_clone_repo(repo_url, directory, branch)

    for rel_path in file_path_list:
        full_path = Path(directory) / rel_path
        find_replace_file_pattern(search_key, replace_value, full_path, suffix)
        commit_msg = f"update {rel_path} {replace_value}"

        pushed = git_commit_and_push_single_file(repo, branch, str(full_path), commit_msg, token, repo_owner, repo_name)
        if not pushed:
            api_update_file(repo_owner, repo_name, branch, token, rel_path, commit_msg)


if __name__ == "__main__":
    main()
