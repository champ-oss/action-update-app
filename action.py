#!/usr/bin/env python
import os
import subprocess
from pathlib import Path
import json
import re
from git import Repo, GitCommandError
from tenacity import retry, stop_after_attempt, wait_fixed


def find_replace_file_pattern(search_string: str, replace_string: str, file_pattern, suffix: str) -> None:
    safe_replace = replace_string.replace("/", "\\/")
    subprocess.call(
        [
            'sed', '-i', '-e', f's/{search_string}:.*/{search_string}:{safe_replace}{suffix}/g', file_pattern
        ]
    )


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
            raise RuntimeError(f"Rebase failed: {e}")
        else:
            raise


@retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
def main():
    repo_path = Path(os.getcwd())  # current workspace from actions/checkout
    file_path_list = json.loads(os.environ.get("FILE_PATH_LIST", "[]"))
    search_key = os.environ.get("SEARCH_KEY")
    replace_value = os.environ.get("REPLACE_VALUE", os.environ.get("GITHUB_SHA"))
    branch_name = os.environ.get("BRANCH", "develop")
    suffix = os.environ.get("SUFFIX", "\"")

    if not file_path_list:
        raise ValueError("FILE_PATH_LIST must be set")

    changed_files = []

    for file_path_str in file_path_list:
        file_path = repo_path / file_path_str
        print(f"[INFO] Processing file: {file_path}")

        updated = find_replace_file_pattern(search_key, replace_value, file_path, suffix)
        if not updated:
            updated = find_replace_with_sed(search_key, replace_value, file_path, suffix)

        if updated:
            changed_files.append(file_path)

    if changed_files:
        commit_msg = f"{search_key}{replace_value}"
        for file_path in changed_files:
            commit_and_push(repo_path, file_path, commit_msg, branch_name)
    else:
        print("No changes to commit.")


if __name__ == "__main__":
    main()
