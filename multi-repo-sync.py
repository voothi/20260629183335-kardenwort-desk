#!/usr/bin/env python3
import os
import sys
import time
import argparse
import subprocess
from pathlib import Path

# Coordinated Repositories Configuration
REPOS = {
    "autohotkey": r"U:\voothi\20240411110510-autohotkey",
    "desk": r"U:\voothi\20260629183335-kardenwort-desk",
    "core": r"U:\voothi\20241223170748-kardenwort",
    "vault": r"U:\voothi.vault"
}

ZID_SCRIPT = r"U:\voothi\20241116203211-zid\zid.py"
DEFAULT_LOG_FILENAME = "multi-repo-sync.md"
GIT_REMOTE = "origin"
LOG_COMMIT_VAL = "both"  # Options: "hash" (commit hash), "msg" (commit message/ZID), "both" (hash (msg))
LOG_FORMAT = "code"  # Options: "table" (Markdown table), "code" (Fenced code block text)

def run_git(repo_path, args):
    try:
        res = subprocess.run(
            ["git"] + args,
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True
        )
        return res.stdout.strip(), res.stderr.strip()
    except subprocess.CalledProcessError as e:
        return None, e.stderr.strip()

def get_zid():
    if not os.path.exists(ZID_SCRIPT):
        # Fallback to generating a timestamp locally if the ZID script is not found
        import datetime
        return datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    try:
        res = subprocess.run(
            ["python", ZID_SCRIPT, "--no-clipboard"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True
        )
        return res.stdout.strip()
    except Exception:
        import datetime
        return datetime.datetime.now().strftime("%Y%m%d%H%M%S")

def cmd_status(args):
    # Print clean Docker/Terraform-like aligned column headers
    print(f"{'REPOSITORY':<15} {'STATUS':<10} {'BRANCH':<20} {'COMMIT':<10} {'TAGS':<20} {'MESSAGE'}")
    
    for name, path in REPOS.items():
        if not os.path.exists(path):
            print(f"{name:<15} {'missing':<10} {'-':<20} {'-':<10} {'-':<20} (Folder not found)")
            continue
            
        branch, _ = run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
        commit_hash, _ = run_git(path, ["log", "-1", "--pretty=format:%h"])
        commit_msg, _ = run_git(path, ["log", "-1", "--pretty=format:%s"])
        tags, _ = run_git(path, ["tag", "--points-at", "HEAD"])
        dirty, _ = run_git(path, ["status", "--porcelain"])
        
        status_str = "dirty" if dirty else "clean"
        branch_str = branch if branch else "detached"
        
        c_hash = commit_hash if commit_hash else "-"
        c_msg = commit_msg if commit_msg else "(No commits)"
        tag_str = ", ".join(tags.splitlines()) if tags else "-"
            
        print(f"{name:<15} {status_str:<10} {branch_str:<20} {c_hash:<10} {tag_str:<20} {c_msg}")

def cmd_tag(args):
    tag_name = args.name
    if not tag_name:
        tag_name = get_zid()
        
    print(f"Creating coordinated tag '{tag_name}' across all repositories...")
    
    # 1. Pre-check dirty repos
    dirty_repos = []
    for name, path in REPOS.items():
        if os.path.exists(path):
            dirty, _ = run_git(path, ["status", "--porcelain"])
            if dirty:
                dirty_repos.append(name)
                
    if dirty_repos:
        print(f"WARNING: The following repositories have uncommitted changes: {', '.join(dirty_repos)}")
        print("The tag will be attached to the latest COMMIT, not the uncommitted workspace changes.")
        if not args.force:
            confirm = input("Do you want to proceed? [y/N]: ").strip().lower()
            if confirm != 'y':
                print("Aborted.")
                sys.exit(1)
                
    # 2. Apply tags
    success = True
    for name, path in REPOS.items():
        if not os.path.exists(path):
            print(f"[-] {name}: Skipped (Path not found)")
            continue
            
        # Create annotated tag
        _, err = run_git(path, ["tag", "-a", tag_name, "-m", f"Coordinated snapshot {tag_name}"])
        if err:
            print(f"[X] {name}: Failed to tag ({err})")
            success = False
        else:
            print(f"[+] {name}: Tagged successfully")
            if args.push:
                print(f"    Pushing tag '{tag_name}' to {GIT_REMOTE}...")
                _, err_push = run_git(path, ["push", GIT_REMOTE, tag_name])
                if err_push and "error:" in err_push:
                    print(f"    [X] Failed to push tag ({err_push})")
                else:
                    print(f"    [+] Tag pushed successfully to {GIT_REMOTE}")
            
    # 3. Log to file if requested
    if success and args.log_file:
        log_tag_to_file(tag_name, args.log_file)

def log_tag_to_file(tag_name, log_path_str):
    import datetime
    log_path = Path(log_path_str)
    
    # If path is a directory or lacks extension, append default filename
    if log_path.is_dir() or log_path_str.endswith(("/", "\\")) or not log_path.suffix:
        log_path = log_path / DEFAULT_LOG_FILENAME
        
    # Get commit identifiers and statuses based on configuration
    hashes = {}
    for name, path in REPOS.items():
        if os.path.exists(path):
            branch, _ = run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
            commit_hash, _ = run_git(path, ["log", "-1", "--pretty=format:%h"])
            commit_msg, _ = run_git(path, ["log", "-1", "--pretty=format:%s"])
            tags, _ = run_git(path, ["tag", "--points-at", "HEAD"])
            dirty, _ = run_git(path, ["status", "--porcelain"])
            
            hashes[name] = {
                "status": "dirty" if dirty else "clean",
                "branch": branch if branch else "detached",
                "hash": commit_hash if commit_hash else "-",
                "tags": ", ".join(tags.splitlines()) if tags else "-",
                "msg": commit_msg if commit_msg else "-"
            }
        else:
            hashes[name] = {
                "status": "missing",
                "branch": "-",
                "hash": "-",
                "tags": "-",
                "msg": "absent"
            }
            
    date_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    write_header = not log_path.exists() or log_path.stat().st_size == 0
    
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            if write_header:
                f.write("# Multi-Repo Sync History\n")
            
            f.write(f"\n## Release {tag_name} ({date_str})\n\n")
            
            if LOG_FORMAT == "code":
                f.write("```text\n")
                f.write(f"{'REPOSITORY':<15} {'STATUS':<10} {'BRANCH':<20} {'COMMIT':<10} {'TAGS':<20} {'MESSAGE'}\n")
                for name in REPOS.keys():
                    status_str = hashes[name]["status"]
                    branch_str = hashes[name]["branch"]
                    c_hash = hashes[name]["hash"]
                    tag_str = hashes[name]["tags"]
                    c_msg = hashes[name]["msg"]
                    f.write(f"{name:<15} {status_str:<10} {branch_str:<20} {c_hash:<10} {tag_str:<20} {c_msg}\n")
                f.write("```\n")
            else:
                f.write("| REPOSITORY | STATUS | BRANCH | COMMIT | TAGS | MESSAGE |\n")
                f.write("| :--- | :--- | :--- | :--- | :--- | :--- |\n")
                for name in REPOS.keys():
                    status_str = hashes[name]["status"]
                    branch_str = hashes[name]["branch"]
                    c_hash = hashes[name]["hash"]
                    tag_str = hashes[name]["tags"]
                    c_msg = hashes[name]["msg"]
                    f.write(f"| {name} | {status_str} | {branch_str} | {c_hash} | {tag_str} | {c_msg} |\n")
                
        print(f"[+] Appended sync snapshot info to {log_path.resolve()}")
    except Exception as e:
        print(f"[X] Failed to write sync log: {e}")

def cmd_checkout(args):
    tag_name = args.name
    if not tag_name:
        print("Error: You must specify a tag/branch name to checkout.")
        sys.exit(1)
        
    print(f"Checking out '{tag_name}' across all repositories...")
    
    # 1. Pre-check dirty repos
    dirty_repos = []
    for name, path in REPOS.items():
        if os.path.exists(path):
            dirty, _ = run_git(path, ["status", "--porcelain"])
            if dirty:
                dirty_repos.append(name)
                
    if dirty_repos and not args.force:
        print(f"ERROR: Cannot checkout because of uncommitted changes in: {', '.join(dirty_repos)}")
        print("Use --force to discard uncommitted changes or stash them first.")
        sys.exit(1)
        
    # 2. Checkout
    for name, path in REPOS.items():
        if not os.path.exists(path):
            print(f"[-] {name}: Skipped (Path not found)")
            continue
            
        checkout_args = ["checkout", tag_name]
        if args.force:
            checkout_args.append("-f")
            
        _, err = run_git(path, checkout_args)
        if err and "error:" in err:
            print(f"[X] {name}: Failed to checkout ({err})")
        else:
            print(f"[+] {name}: Checked out successfully")

def cmd_delete(args):
    tag_name = args.name
    if not tag_name:
        print("Error: You must specify a tag name to delete.")
        sys.exit(1)
        
    print(f"Deleting tag '{tag_name}' across all repositories...")
    for name, path in REPOS.items():
        if not os.path.exists(path):
            continue
            
        _, err = run_git(path, ["tag", "-d", tag_name])
        if err and "error:" in err:
            print(f"[X] {name}: Failed to delete tag ({err})")
        else:
            print(f"[+] {name}: Tag deleted successfully")

def cmd_commit(args):
    print("Coordinating commits across repositories...")
    
    # 1. Identify dirty repositories
    dirty_repos = []
    for name, path in REPOS.items():
        if os.path.exists(path):
            dirty, _ = run_git(path, ["status", "--porcelain"])
            if dirty:
                dirty_repos.append((name, path))
                
    if not dirty_repos:
        print("No uncommitted changes found in any repository. Nothing to commit.")
        return
        
    print(f"Found uncommitted changes in: {', '.join(name for name, _ in dirty_repos)}")
    
    # 2. Perform commits
    committed_any = False
    last_zid = None
    for i, (name, path) in enumerate(dirty_repos):
        if i > 0:
            print("Sleeping 1.1 seconds to guarantee a unique ZID timestamp...")
            time.sleep(1.1)
            
        last_zid = get_zid()
        print(f"[{name}] Staging changes and committing with message '{last_zid}'...")
        
        # Stage all changes (add untracked and modified)
        _, err_add = run_git(path, ["add", "-A"])
        if err_add:
            print(f"[X] {name}: Failed to stage changes ({err_add})")
            continue
            
        # Commit with ZID as message
        _, err_commit = run_git(path, ["commit", "-m", last_zid])
        if err_commit and "error:" in err_commit:
            print(f"[X] {name}: Failed to commit ({err_commit})")
        else:
            print(f"[+] {name}: Committed successfully with ZID {last_zid}")
            committed_any = True
            
    # 3. Log to file if requested
    if committed_any and args.log_file and last_zid:
        log_tag_to_file(last_zid, args.log_file)

def main():
    parser = argparse.ArgumentParser(description="Coordinated repository sync, tag, and checkout manager.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # status subcommand
    subparsers.add_parser("status", help="Show current branch, status, and tags across repositories.")
    
    # tag subcommand
    parser_tag = subparsers.add_parser("tag", help="Create a coordinated tag across all repositories.")
    parser_tag.add_argument("name", nargs="?", help="Tag name. Defaults to current ZID if omitted.")
    parser_tag.add_argument("-f", "--force", action="store_true", help="Force tag creation without confirmation on dirty worktrees.")
    parser_tag.add_argument("-l", "--log-file", help="Path to markdown tag history log file (e.g. ./ or ./coordinated_tags.md).")
    parser_tag.add_argument("-p", "--push", action="store_true", help="Push tags to remote origin repository.")
    
    # commit subcommand
    parser_commit = subparsers.add_parser("commit", help="Commit dirty repositories sequentially with unique ZIDs.")
    parser_commit.add_argument("-l", "--log-file", help="Path to markdown history log file to record post-commit hashes.")
    
    # checkout subcommand
    parser_checkout = subparsers.add_parser("checkout", help="Checkout a specific tag/branch across all repositories.")
    parser_checkout.add_argument("name", help="Tag or branch name to checkout.")
    parser_checkout.add_argument("-f", "--force", action="store_true", help="Force checkout (discarding local changes).")
    
    # delete subcommand
    parser_delete = subparsers.add_parser("delete", help="Delete a specific tag across all repositories.")
    parser_delete.add_argument("name", help="Tag name to delete.")
    
    args = parser.parse_args()
    
    if args.command == "status":
        cmd_status(args)
    elif args.command == "tag":
        cmd_tag(args)
    elif args.command == "commit":
        cmd_commit(args)
    elif args.command == "checkout":
        cmd_checkout(args)
    elif args.command == "delete":
        cmd_delete(args)

if __name__ == "__main__":
    main()
