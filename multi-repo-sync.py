#!/usr/bin/env python3
import os
import sys
import time
import argparse
import subprocess
from pathlib import Path

# Coordinated Repositories Configuration
REPOS = {
    "desk": r"U:\voothi\20260629183335-kardenwort-desk",
    "autohotkey": r"U:\voothi\20240411110510-autohotkey",
    "core": r"U:\voothi\20241223170748-kardenwort",
    "goldendict": r"U:\voothi\20260113230706-goldendict",
    "vault": r"U:\voothi.vault"
}

ZID_SCRIPT = r"U:\voothi\20241116203211-zid\zid.py"
DEFAULT_LOG_FILENAME = "multi-repo-sync.md"
GIT_REMOTE = "origin"
LOG_COMMIT_VAL = "both"  # Options: "hash" (commit hash), "msg" (commit message/ZID), "both" (hash (msg))
LOG_FORMAT = "code"  # Options: "table" (Markdown table), "code" (Fenced code block text), "log" (Plain text log line)

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
    # Collect all repository status details
    rows = []
    for name, path in REPOS.items():
        if not os.path.exists(path):
            rows.append({
                "name": name, "status": "missing", "branch": "-",
                "hash": "-", "tags": "-", "msg": "(Folder not found)"
            })
            continue
            
        branch, _ = run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
        commit_hash, _ = run_git(path, ["log", "-1", "--pretty=format:%h"])
        commit_msg, _ = run_git(path, ["log", "-1", "--pretty=format:%s"])
        tags, _ = run_git(path, ["tag", "--points-at", "HEAD"])
        dirty, _ = run_git(path, ["status", "--porcelain"])
        
        rows.append({
            "name": name,
            "status": "dirty" if dirty else "clean",
            "branch": branch if branch else "detached",
            "hash": commit_hash if commit_hash else "-",
            "tags": ", ".join(tags.splitlines()) if tags else "-",
            "msg": commit_msg if commit_msg else "(No commits)"
        })
        
    # Dynamically compute column widths
    w_name = max(max(len(r["name"]) for r in rows), len("REPOSITORY"))
    w_status = max(max(len(r["status"]) for r in rows), len("STATUS"))
    w_branch = max(max(len(r["branch"]) for r in rows), len("BRANCH"))
    w_hash = max(max(len(r["hash"]) for r in rows), len("COMMIT"))
    w_tags = max(max(len(r["tags"]) for r in rows), len("TAGS"))
    
    # Print aligned columns
    print(f"{'REPOSITORY':<{w_name}} {'STATUS':<{w_status}} {'BRANCH':<{w_branch}} {'COMMIT':<{w_hash}} {'TAGS':<{w_tags}} {'MESSAGE'}")
    for r in rows:
        print(f"{r['name']:<{w_name}} {r['status']:<{w_status}} {r['branch']:<{w_branch}} {r['hash']:<{w_hash}} {r['tags']:<{w_tags}} {r['msg']}")

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
        for log_path in args.log_file:
            log_tag_to_file(tag_name, log_path, log_format=args.log_format)

def log_tag_to_file(tag_name, log_path_str, log_format=None):
    import datetime
    log_path = Path(log_path_str)
    
    # 1. Resolve log_format orthogonally: fall back to file suffix if format is not explicitly passed
    resolved_format = log_format
    if resolved_format is None:
        if log_path.suffix.lower() == ".log":
            resolved_format = "log"
        elif log_path.suffix.lower() == ".md":
            resolved_format = LOG_FORMAT if LOG_FORMAT != "log" else "code"
        else:
            resolved_format = LOG_FORMAT
            
    # 2. If path is a directory or lacks suffix, append default filename
    if log_path.is_dir() or log_path_str.endswith(("/", "\\")) or not log_path.suffix:
        default_name = "multi-repo-sync.log" if resolved_format == "log" else DEFAULT_LOG_FILENAME
        log_path = log_path / default_name
        
    # Re-verify format if we appended default filename and log_format was not explicitly passed
    if log_format is None:
        if log_path.suffix.lower() == ".log":
            resolved_format = "log"
        else:
            resolved_format = LOG_FORMAT if LOG_FORMAT != "log" else "code"
            
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
        
        if resolved_format in ("log", "table"):
            with open(log_path, "a", encoding="utf-8") as f:
                if resolved_format == "log":
                    # Flat single-line log format (perfect for sorting by ZID)
                    parts = [f"{tag_name}", f"[{date_str}]"]
                    for name in REPOS.keys():
                        if hashes[name]["status"] == "missing":
                            parts.append(f"{name}:absent")
                        else:
                            c_hash = hashes[name]["hash"]
                            c_msg = hashes[name]["msg"]
                            if LOG_COMMIT_VAL == "msg":
                                val = c_msg
                            elif LOG_COMMIT_VAL == "both":
                                val = f"{c_hash}({c_msg})"
                            else:  # "hash"
                                val = c_hash
                            parts.append(f"{name}:{val}")
                    f.write(" ".join(parts) + "\n")
                elif resolved_format == "table":
                    # Flat horizontal table (perfect for sorting by ZID)
                    repo_names = list(REPOS.keys())
                    if write_header:
                        f.write("# Multi-Repo Sync History\n\n")
                        headers = ["Tag / ZID", "Date"] + repo_names
                        alignments = [":---"] * len(headers)
                        f.write("| " + " | ".join(headers) + " |\n")
                        f.write("| " + " | ".join(alignments) + " |\n")
                    
                    row_data = [tag_name, date_str]
                    for name in repo_names:
                        if hashes[name]["status"] == "missing":
                            row_data.append("absent")
                        else:
                            c_hash = hashes[name]["hash"]
                            c_msg = hashes[name]["msg"]
                            if LOG_COMMIT_VAL == "msg":
                                row_data.append(c_msg)
                            elif LOG_COMMIT_VAL == "both":
                                row_data.append(f"{c_hash} ({c_msg})")
                            else:  # "hash"
                                row_data.append(c_hash)
                    
                    f.write("| " + " | ".join(row_data) + " |\n")
        else:
            # Vertical/detailed format per release (code block or vertical table)
            # Parse existing sections to maintain chronological TOC
            sections = []
            if log_path.exists() and log_path.stat().st_size > 0:
                with open(log_path, "r", encoding="utf-8") as f:
                    content = f.read()
                parts = content.split("## Release ")
                for part in parts[1:]:
                    lines = part.strip().splitlines()
                    if not lines:
                        continue
                    header_line = lines[0].strip()
                    body = "\n".join(lines[1:])
                    # Strip existing navigation links
                    if "[Return to Top]" in body:
                        body = body.split("[Return to Top]")[0].strip()
                    
                    tag_name_extracted = header_line.split("(")[0].strip()
                    date_str_extracted = ""
                    if "(" in header_line:
                        date_str_extracted = header_line.split("(")[1].replace(")", "").strip()
                    
                    sections.append({
                        "tag": tag_name_extracted,
                        "date": date_str_extracted,
                        "body": body.strip()
                    })
            
            # Generate the new section body
            new_body = ""
            if resolved_format == "code":
                w_name = max(max(len(name) for name in REPOS.keys()), len("REPOSITORY"))
                w_status = max(max(len(hashes[name]["status"]) for name in REPOS.keys()), len("STATUS"))
                w_branch = max(max(len(hashes[name]["branch"]) for name in REPOS.keys()), len("BRANCH"))
                w_hash = max(max(len(hashes[name]["hash"]) for name in REPOS.keys()), len("COMMIT"))
                w_tags = max(max(len(hashes[name]["tags"]) for name in REPOS.keys()), len("TAGS"))
                
                new_body += "```text\n"
                new_body += f"{'REPOSITORY':<{w_name}} {'STATUS':<{w_status}} {'BRANCH':<{w_branch}} {'COMMIT':<{w_hash}} {'TAGS':<{w_tags}} {'MESSAGE'}\n"
                for name in REPOS.keys():
                    status_str = hashes[name]["status"]
                    branch_str = hashes[name]["branch"]
                    c_hash = hashes[name]["hash"]
                    tag_str = hashes[name]["tags"]
                    c_msg = hashes[name]["msg"]
                    new_body += f"{name:<{w_name}} {status_str:<{w_status}} {branch_str:<{w_branch}} {c_hash:<{w_hash}} {tag_str:<{w_tags}} {c_msg}\n"
                new_body += "```"
            else:
                new_body += "| REPOSITORY | STATUS | BRANCH | COMMIT | TAGS | MESSAGE |\n"
                new_body += "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
                for name in REPOS.keys():
                    status_str = hashes[name]["status"]
                    branch_str = hashes[name]["branch"]
                    c_hash = hashes[name]["hash"]
                    tag_str = hashes[name]["tags"]
                    c_msg = hashes[name]["msg"]
                    new_body += f"| {name} | {status_str} | {branch_str} | {c_hash} | {tag_str} | {c_msg} |\n"
            
            sections.append({
                "tag": tag_name,
                "date": date_str,
                "body": new_body.strip()
            })
            
            # Write the reconstructed document back to the file
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("# Multi-Repo Sync History\n\n")
                f.write("## Table of Contents\n")
                for s in sections:
                    anchor = f"release-{s['tag']}".lower().replace(" ", "-")
                    anchor = "".join(c for c in anchor if c.isalnum() or c in "-_")
                    f.write(f"- [Release {s['tag']} ({s['date']})](#{anchor})\n")
                f.write("\n")
                
                for s in sections:
                    f.write(f"## Release {s['tag']} ({s['date']})\n\n")
                    f.write(s["body"] + "\n\n")
                    f.write("[Return to Top](#table-of-contents)\n\n")
                    
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
        for log_path in args.log_file:
            log_tag_to_file(last_zid, log_path, log_format=args.log_format)

def main():
    parser = argparse.ArgumentParser(description="Coordinated repository sync, tag, and checkout manager.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # status subcommand
    subparsers.add_parser("status", help="Show current branch, status, and tags across repositories.")
    
    # tag subcommand
    parser_tag = subparsers.add_parser("tag", help="Create a coordinated tag across all repositories.")
    parser_tag.add_argument("name", nargs="?", help="Tag name. Defaults to current ZID if omitted.")
    parser_tag.add_argument("-f", "--force", action="store_true", help="Force tag creation without confirmation on dirty worktrees.")
    parser_tag.add_argument("-l", "--log-file", nargs="+", help="One or more paths to markdown history log files to record sync snapshots.")
    parser_tag.add_argument("-p", "--push", action="store_true", help="Push tags to remote origin repository.")
    parser_tag.add_argument("--log-format", choices=["table", "code", "log"], default=None, help="Logging format. Overrides LOG_FORMAT.")
    
    # commit subcommand
    parser_commit = subparsers.add_parser("commit", help="Commit dirty repositories sequentially with unique ZIDs.")
    parser_commit.add_argument("-l", "--log-file", nargs="+", help="One or more paths to history log files to record post-commit hashes.")
    parser_commit.add_argument("--log-format", choices=["table", "code", "log"], default=None, help="Logging format. Overrides LOG_FORMAT.")
    
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
