#!/usr/bin/env python3
import os
import subprocess
import sys
import json
from pathlib import Path
import logging
from git import Repo, GitCommandError

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


def find_replace_file_pattern(search_string: str, replace_string: str, file_path: str, suffix: str = '"') -> bool:
    """Find and replace a line starting with search_string: in file. Returns True if file changed."""
    file = Path(file_path)
    if not file.exists():
        logging.error("File does not exist: %s", file)
        return False

    # Escape slashes in git SHA
    safe_replace = replace_string.replace("/", "\\/")
    sed_expr = f's/{search_string}:.*/{search_string}:{safe_replace}{suffix}/g'

    before = file.read_text()
    subprocess.call(['sed', '-i', '-e', sed_expr, str(file)])
    after = file.read_text()

    if before != after:
        logging.info("Updated %s successfully", file)
        return True
    logging.info("No changes for %s", file)
    return False


def commit_and_push(repo_dir: str, files: list[Path], branch_name: str = "main"):
    """Commit and push changes to the branch."""
    repo = Repo(repo_dir)
    repo.git.checkout(branch_name)

    try:
        repo.git.pull("--rebase")
        rel_paths = [str(f.relative_to(repo_dir)) for f in files]
        repo.index.add(rel_paths)
        repo.index.commit(f"Updated files: {', '.join(rel_paths)}")
        repo.remote().push()
        logging.info("Changes pushed successfully")
    except GitCommandError as e:
        logging.error("Git error: %s", e)
        try:
            repo.git.rebase("--abort")
        except GitCommandError:
            pass
        sys.exit(1)


def main():
    repo_dir = Path(os.environ.get("GITHUB_WORKSPACE", os.getcwd()))
    file_path_list = json.loads(os.environ.get("FILE_PATH_LIST", "[]"))
    search_string = os.environ.get("SEARCH_STRING", "git_sha")
    replace_string = os.environ.get("REPLACE_STRING")
    branch_name = os.environ.get("BRANCH", "develop")
    suffix = os.environ.get("SUFFIX", '"')

    if not replace_string or not file_path_list:
        logging.error("REPLACE_STRING and FILE_PATH_LIST must be set")
        sys.exit(1)

    changed_files = []

    for rel_path in file_path_list:
        abs_path = repo_dir / rel_path
        if find_replace_file_pattern(search_string, replace_string, abs_path, suffix):
            changed_files.append(abs_path)

    if changed_files:
        commit_and_push(repo_dir, changed_files, branch_name)
    else:
        logging.info("No changes to commit")


if __name__ == "__main__":
    main()
