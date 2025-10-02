#!/usr/bin/env python3
# Description: Update files in GitHub repo using GitHub App token with rebase + retry

import json
import subprocess
import time
import os
from pathlib import Path

def run(cmd, cwd=None, check=True):
    """Run shell command via subprocess."""
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd)
    if check and result.returncode != 0:
        raise Exception(f"Command failed: {cmd}")
    return result

def find_replace_file_pattern(search_string: str, replace_string: str, file_path: Path, suffix: str):
    """Update the file in-place using sed-style replacement."""
    run(f'sed -i "s/{search_string}:.*$/{search_string}:{replace_string}{suffix}/" {file_path}')

def git_push_with_rebase(repo_dir: str, branch_name: str, max_attempts: int = 5, delay: int = 5):
    """Push changes with automatic rebase retry if push fails."""
    for attempt in range(1, max_attempts + 1):
        try:
            run(f"git push origin {branch_name}", cwd=repo_dir)
            print("Push successful")
            return
        except Exception as e:
            print(f"Push failed on attempt {attempt}: {e}")
            print("Attempting rebase...")
            run(f"git pull --rebase origin {branch_name}", cwd=repo_dir)
            time.sleep(delay)
    raise Exception(f"Failed to push after {max_attempts} attempts")

def main():
    branch_name = os.environ.get('BRANCH', 'main')
    repo_owner_target = os.environ.get('GITHUB_REPOSITORY').split('/')[0]
    repo_name_target = os.environ.get('GITHUB_REPOSITORY').split('/')[1]
    repo_dir = os.environ.get('GIT_LOCAL_DIRECTORY', repo_name_target)
    search_string = os.environ.get('SEARCH_KEY', repo_name_target)
    file_path_list = json.loads(os.environ['FILE_PATH_LIST'])
    gh_sha = os.environ.get('GITHUB_SHA')
    replace_value = os.environ.get('REPLACE_VALUE', gh_sha)
    suffix = os.environ.get('SUFFIX', '"')

    github_token = os.environ.get('GITHUB_APP_TOKEN')
    if not github_token:
        raise Exception("GITHUB_APP_TOKEN not found in environment")

    repo_url = f"https://x-access-token:{github_token}@github.com/{repo_owner_target}/{repo_name_target}.git"

    # Clean workspace
    if os.path.exists(repo_dir):
        run(f"rm -rf {repo_dir}")

    # Clone repo
    run(f"git clone {repo_url} {repo_dir} --branch {branch_name}")

    # Update files
    files_changed = False
    for file_pattern in file_path_list:
        file_path = Path(repo_dir) / file_pattern
        if not file_path.exists():
            print(f"File not found: {file_path}")
            continue
        find_replace_file_pattern(search_string, replace_value, file_path, suffix)
        # Check if file content changed
        diff = subprocess.run(f"git diff {file_path}", shell=True, cwd=repo_dir)
        if diff.returncode == 1:  # git diff returns 1 if changes exist
            files_changed = True
            print(f"File modified: {file_pattern}")

    if files_changed:
        # Commit changes
        run("git add .", cwd=repo_dir)
        commit_message = f"Update {search_string}-{gh_sha}"
        run(f"git commit -m \"{commit_message}\"", cwd=repo_dir)
        print(f"Committed changes: {commit_message}")

        # Push with rebase retry
        git_push_with_rebase(repo_dir, branch_name)
    else:
        print("No changes detected. Nothing to commit.")

if __name__ == "__main__":
    main()
