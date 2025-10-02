#!/usr/bin/env python3

import os
import subprocess
import shutil
from pathlib import Path
import json
import re
from git import Repo, GitCommandError
from tenacity import retry, stop_after_attempt, wait_fixed


def run_cmd(cmd: list, cwd: Path = None):
    """Run a shell command with optional working directory."""
    print(f"[CMD] {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if result.returncode != 0:
        print(f"[ERROR] {result.stderr}")
        raise RuntimeError(f"Command failed: {' '.join(cmd)}")
    return result.stdout.strip()


def find_replace_file_pattern(search_string: str, replace_string: str, file_path: Path, suffix: str = "\"") -> bool:
    """
    Find and replace a line starting with `search_string:` in the given file.
    Returns True if a change was made, else False.
    """
    if not file_path.exists():
        print(f"[WARN] File not found: {file_path}")
        return False

    with open(file_path, "r") as f:
        content = f.read()

    # Pattern: search for `search_string:<anything>`
    pattern = rf"({re.escape(search_string)}:).*"
    replacement = rf"\1{replace_string}{suffix}"

    new_content = re.sub(pattern, replacement, content)

    if new_content != content:
        with open(file_path, "w") as f:
            f.write(new_content)
        print(f"[INFO] Updated {file_path} with {search_string}:{replace_string}{suffix}")
        return True

    print(f"[INFO] No changes needed for {file_path}")
    return False


def find_replace_with_sed(search_string: str, replace_string: str, file_path: Path, suffix: str = "\"") -> bool:
    """
    Alternative using `sed` for in-place replacement.
    Returns True if sed made a change.
    """
    before = file_path.read_text()
    subprocess.call([
        "sed", "-i",
        f"s/{search_string}:.*/{search_string}:{replace_string}{suffix}/g",
        str(file_path)
    ])
    after = file_path.read_text()
    return before != after


def commit_and_push(repo_path: Path, file_path: Path, commit_message: str, branch_name: str = "main"):
    """Commit and push changes to git, handling conflicts."""
    repo = Repo(repo_path)
    repo.git.checkout(branch_name)

    try:
        repo.git.pull("--rebase")
        # ✅ FIX: ensure we use relative path to repo root
        rel_path = str(file_path.relative_to(repo_path))
        repo.index.add([rel_path])
        repo.index.commit(commit_message)
        repo.remote().push()
        print(f"[INFO] Successfully pushed changes for {rel_path}")
    except GitCommandError as e:
        if "CONFLICT" in str(e) or "rebase" in str(e):
            print(f"[ERROR] Rebase conflict detected: {e}")
            try:
                repo.git.rebase("--abort")
            except GitCommandError:
                pass
            print(f"[INFO] Cleaning local repo due to conflict: {repo_path}")
            shutil.rmtree(repo_path)
            raise RuntimeError(f"Rebase failed and repo cleaned: {e}")
        else:
            raise


@retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
def main():
    github_repo_target = os.environ.get("GITHUB_REPO_TARGET")
    file_path_list = json.loads(os.environ.get("FILE_PATH_LIST", "[]"))
    search_key = os.environ.get("SEARCH_KEY")
    replace_value = os.environ.get("REPLACE_VALUE", os.environ.get("GITHUB_SHA"))
    branch_name = os.environ.get("BRANCH", "develop")
    suffix = os.environ.get("SUFFIX", "\"")

    if not github_repo_target:
        raise ValueError("GITHUB_REPO_TARGET must be set")

    # Clone target repo
    repo_name = github_repo_target.split("/")[-1].replace(".git", "")
    git_local_dir = Path(repo_name)
    if git_local_dir.exists():
        shutil.rmtree(git_local_dir)

    print(f"Cloning repo: {github_repo_target} -> {git_local_dir}")
    Repo.clone_from(github_repo_target, git_local_dir, branch=branch_name)

    changed_files = []

    for file_path_str in file_path_list:
        file_path = git_local_dir / file_path_str
        print(f"[INFO] Processing file: {file_path}")

        updated = find_replace_file_pattern(search_key, replace_value, file_path, suffix)
        # or fallback to sed if regex fails
        if not updated:
            updated = find_replace_with_sed(search_key, replace_value, file_path, suffix)

        if updated:
            changed_files.append(file_path)

    if changed_files:
        commit_msg = f"{search_key}{replace_value}"
        for file_path in changed_files:
            commit_and_push(git_local_dir, file_path, commit_msg, branch_name)
    else:
        print("No changes to commit.")


if __name__ == "__main__":
    main()
