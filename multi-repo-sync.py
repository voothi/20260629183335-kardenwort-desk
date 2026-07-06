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
    "multi-repo-sync": r"U:\voothi\20260706123954-multi-repo-sync",
    "vault": r"U:\voothi.vault"
}

ZID_SCRIPT = r"U:\voothi\20241116203211-zid\zid.py"
DEFAULT_LOG_FILENAME = "multi-repo-sync.md"
DEFAULT_LOG_PATHS = [r"U:\voothi.vault\multi-repo-sync.md"]    # Default log paths to record sync history if -l/--log-file is omitted (e.g. [r"U:\voothi.vault\multi-repo-sync.md"])
GIT_REMOTE = "origin"
LOG_COMMIT_VAL = "both"  # Options: "hash" (commit hash), "msg" (commit message/ZID), "both" (hash (msg))
LOG_FORMAT = "code"  # Options: "table" (Markdown table), "code" (Fenced code block text), "log" (Plain text log line)
DEFAULT_CWD = r"U:\voothi\20260629183335-kardenwort-desk"   # Default working directory context (None means use shell's current directory)
DEFAULT_TAG_NAME_TEMPLATE = "{zid}-snapshot-desk"
DEFAULT_TAG_MSG_TEMPLATE = "Coordinated snapshot {zid} to desk"
DEFAULT_COMMIT_MSG_TEMPLATE = "{zid} to desk"
REVERSE_TAGS_ORDER = True  # Display tags starting from the latest/last (True) or alphabetical/chronological order (False)
STATUS_COLUMNS = ["REPOSITORY", "STATUS", "COMMIT", "TAGS", "MESSAGE"]  # Options: "REPOSITORY", "STATUS", "BRANCH", "COMMIT", "TAGS", "MESSAGE"

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

def format_tags(tags_list, max_len=40):
    if not tags_list:
        return "-"
    joined = ", ".join(tags_list)
    if len(joined) <= max_len:
        return joined
        
    result = []
    current_len = 0
    for tag in tags_list:
        extra = 5 if result else 3  # ", ..." vs "..."
        if current_len + len(tag) + extra > max_len:
            break
        result.append(tag)
        current_len += len(tag) + (2 if len(result) > 1 else 0)
        
    if not result:
        return tags_list[0][:max_len-3] + "..."
        
    return ", ".join(result) + ", ..."

def cmd_status(args):
    # Collect all repository status details
    rows = []
    for name, path in REPOS.items():
        if not os.path.exists(path):
            rows.append({
                "REPOSITORY": name,
                "STATUS": "missing",
                "BRANCH": "-",
                "COMMIT": "-",
                "TAGS": "-",
                "MESSAGE": "(path not found)"
            })
            continue
            
        branch, _ = run_git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
        commit_hash, _ = run_git(path, ["log", "-1", "--pretty=format:%h"])
        commit_msg, _ = run_git(path, ["log", "-1", "--pretty=format:%s"])
        tags, _ = run_git(path, ["tag", "--points-at", "HEAD"])
        dirty, _ = run_git(path, ["status", "--porcelain"])
        
        tags_list = tags.splitlines() if tags else []
        if REVERSE_TAGS_ORDER:
            tags_list.reverse()
        
        rows.append({
            "REPOSITORY": name,
            "STATUS": "dirty" if dirty else "clean",
            "BRANCH": branch if branch else "detached",
            "COMMIT": commit_hash if commit_hash else "-",
            "TAGS": format_tags(tags_list),
            "MESSAGE": commit_msg if commit_msg else "(No commits)"
        })
        
    # Dynamically compute column widths for enabled columns
    widths = {}
    for col in STATUS_COLUMNS:
        widths[col] = max(max(len(r.get(col, "")) for r in rows), len(col))
    
    # Print aligned columns
    header_parts = []
    for idx, col in enumerate(STATUS_COLUMNS):
        if idx == len(STATUS_COLUMNS) - 1:
            header_parts.append(col)
        else:
            header_parts.append(f"{col:<{widths[col]}}")
    print(" ".join(header_parts))
    
    for r in rows:
        row_parts = []
        for idx, col in enumerate(STATUS_COLUMNS):
            val = r.get(col, "")
            if idx == len(STATUS_COLUMNS) - 1:
                row_parts.append(val)
            else:
                row_parts.append(f"{val:<{widths[col]}}")
        print(" ".join(row_parts))

def cmd_tag(args):
    zid_val = get_zid()
    
    # Resolve tag name template
    tag_name_template = getattr(args, "name", None)
    if not tag_name_template:
        tag_name_template = DEFAULT_TAG_NAME_TEMPLATE
    tag_name = tag_name_template.format(zid=zid_val)
    
    # Resolve tag message template
    tag_msg_template = getattr(args, "message", None)
    if not tag_msg_template:
        tag_msg_template = DEFAULT_TAG_MSG_TEMPLATE
    tag_msg = tag_msg_template.format(zid=zid_val, tag_name=tag_name)
        
    print(f"sync: Creating coordinated tag [name={tag_name}]")
    
    # 1. Pre-check dirty repos
    dirty_repos = []
    for name, path in REPOS.items():
        if os.path.exists(path):
            dirty, _ = run_git(path, ["status", "--porcelain"])
            if dirty:
                dirty_repos.append(name)
                
    if dirty_repos:
        print(f"sync: Warning - The following repositories have uncommitted changes: {', '.join(dirty_repos)}")
        print("sync: The tag will be attached to the latest commit, not the uncommitted workspace changes.")
        if not args.force:
            confirm = input("sync: Do you want to proceed? [y/N]: ").strip().lower()
            if confirm != 'y':
                print("sync: Aborted.")
                sys.exit(1)
                
    # 2. Apply tags
    success = True
    for name, path in REPOS.items():
        if not os.path.exists(path):
            print(f"{name}: Skipped (path not found)")
            continue
            
        # Create annotated tag
        _, err = run_git(path, ["tag", "-a", tag_name, "-m", tag_msg])
        if err:
            print(f"{name}: Error - Failed to tag ({err})")
            success = False
        else:
            print(f"{name}: Tag complete")
            if args.push:
                print(f"{name}: Pushing tag [remote={GIT_REMOTE}]...")
                _, err_push = run_git(path, ["push", GIT_REMOTE, tag_name])
                if err_push and "error:" in err_push:
                    print(f"{name}: Error - Failed to push tag ({err_push})")
                else:
                    print(f"{name}: Push complete")
            
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
            
            tags_list = tags.splitlines() if tags else []
            if REVERSE_TAGS_ORDER:
                tags_list.reverse()
            hashes[name] = {
                "status": "dirty" if dirty else "clean",
                "branch": branch if branch else "detached",
                "hash": commit_hash if commit_hash else "-",
                "tags": format_tags(tags_list),
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
                widths = {}
                for col in STATUS_COLUMNS:
                    widths[col] = max(max(len(hashes[n].get(col, "")) for n in REPOS.keys()), len(col))
                
                new_body += "```text\n"
                header_parts = []
                for idx, col in enumerate(STATUS_COLUMNS):
                    if idx == len(STATUS_COLUMNS) - 1:
                        header_parts.append(col)
                    else:
                        header_parts.append(f"{col:<{widths[col]}}")
                new_body += " ".join(header_parts) + "\n"
                
                for name in REPOS.keys():
                    row_parts = []
                    for idx, col in enumerate(STATUS_COLUMNS):
                        val = hashes[name].get(col, "")
                        if idx == len(STATUS_COLUMNS) - 1:
                            row_parts.append(val)
                        else:
                            row_parts.append(f"{val:<{widths[col]}}")
                    new_body += " ".join(row_parts) + "\n"
                new_body += "```"
            else:
                new_body += "| " + " | ".join(STATUS_COLUMNS) + " |\n"
                new_body += "| " + " | ".join([":---"] * len(STATUS_COLUMNS)) + " |\n"
                for name in REPOS.keys():
                    row_data = [hashes[name].get(col, "") for col in STATUS_COLUMNS]
                    new_body += "| " + " | ".join(row_data) + " |\n"
            
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
                    
        print(f"sync: Appended snapshot info [file={log_path.resolve()}]")
    except Exception as e:
        print(f"sync: Error - Failed to write sync log ({e})")

def cmd_checkout(args):
    tag_name = args.name
    if not tag_name:
        print("sync: Error - You must specify a tag/branch name to checkout.")
        sys.exit(1)
    # 1. Pre-check dirty repos
    dirty_repos = []
    for name, path in REPOS.items():
        if os.path.exists(path):
            dirty, _ = run_git(path, ["status", "--porcelain"])
            if dirty:
                dirty_repos.append(name)
                
    if dirty_repos and not args.force:
        print(f"sync: Error - Cannot checkout because of uncommitted changes in: {', '.join(dirty_repos)}")
        print("sync: Use -f/--force to discard uncommitted changes or stash them first.")
        sys.exit(1)
        
    print(f"sync: Checking out [target={tag_name}]")
    for name, path in REPOS.items():
        if not os.path.exists(path):
            print(f"{name}: Skipped (path not found)")
            continue
            
        # Check if repo is dirty
        dirty, _ = run_git(path, ["status", "--porcelain"])
        if dirty and not args.force:
            print(f"{name}: Error - Repository has uncommitted changes. Use -f to force.")
            continue
            
        cmd = ["checkout", tag_name]
        if args.force:
            cmd.append("-f")
            
        _, err = run_git(path, cmd)
        if err and "error:" in err:
            print(f"{name}: Error - Failed to checkout ({err})")
        else:
            print(f"{name}: Checkout complete")

def cmd_delete(args):
    tag_name = args.name
    if not tag_name:
        print("sync: Error - You must specify a tag name to delete.")
        sys.exit(1)
        
    print(f"sync: Deleting tag [target={tag_name}]")
    for name, path in REPOS.items():
        if not os.path.exists(path):
            continue
            
        _, err = run_git(path, ["tag", "-d", tag_name])
        if err and "error:" in err:
            print(f"{name}: Error - Failed to delete tag ({err})")
        else:
            print(f"{name}: Delete complete")

def cmd_commit(args):
    print("sync: Evaluating repositories for commit...")
    
    # 1. Identify dirty repositories
    dirty_repos = []
    for name, path in REPOS.items():
        if os.path.exists(path):
            dirty, _ = run_git(path, ["status", "--porcelain"])
            if dirty:
                dirty_repos.append((name, path))
                
    if not dirty_repos:
        print("sync: No uncommitted changes found. Nothing to commit.")
        return
        
    print(f"sync: Uncommitted changes detected in: {', '.join(name for name, _ in dirty_repos)}")
    
    # 2. Perform commits
    committed_any = False
    last_zid = None
    for i, (name, path) in enumerate(dirty_repos):
        if i > 0:
            print("sync: Sleeping 1.1s for unique timestamp...")
            time.sleep(1.1)
            
        last_zid = get_zid()
        msg_template = getattr(args, "message", None)
        if not msg_template:
            msg_template = DEFAULT_COMMIT_MSG_TEMPLATE
            
        # To maintain perfect orthogonality with tag, commit template resolving also supports {tag_name} if passed globally
        fallback_tag_name = getattr(args, "name", None)
        if not fallback_tag_name:
            fallback_tag_name = last_zid
        else:
            fallback_tag_name = fallback_tag_name.format(zid=last_zid)
            
        commit_msg = msg_template.format(zid=last_zid, tag_name=fallback_tag_name)
        
        print(f"\n{name}: Staging changes and committing...")
        
        # Stage all changes (add untracked and modified)
        _, err_add = run_git(path, ["add", "-A"])
        if err_add:
            print(f"{name}: Error - Failed to stage changes ({err_add})")
            continue
            
        # Commit with resolved message
        _, err_commit = run_git(path, ["commit", "-m", commit_msg])
        if err_commit and "error:" in err_commit:
            print(f"{name}: Error - Failed to commit ({err_commit})")
        else:
            print(f"{name}: Commit complete [msg={commit_msg}]")
            committed_any = True
            
    # 3. Log to file if requested
    if committed_any and args.log_file and last_zid:
        for log_path in args.log_file:
            log_tag_to_file(last_zid, log_path, log_format=args.log_format)

def cmd_sync(args):
    print("sync: Starting commit phase...")
    
    # Temporarily disable logging for commit to avoid duplicate log entries
    original_log_file = getattr(args, "log_file", None)
    args.log_file = None
    
    # 1. Commit
    cmd_commit(args)
    
    # Give a tiny bit of breathing room before generating a new ZID for the tag
    print("\nsync: Sleeping 1.1s for unique tag timestamp...")
    time.sleep(1.1)
    
    print("\nsync: Starting tag phase...")
    # Restore log_file for tag step
    args.log_file = original_log_file
    
    # Make sure we don't accidentally reuse an explicit name for the tag if we didn't intend to
    # If name wasn't provided, cmd_tag will generate a fresh ZID.
    cmd_tag(args)

def main():
    parser = argparse.ArgumentParser(
        description="Coordinated repository sync, tag, and checkout manager.",
        epilog="""
subcommand options:
  global
    -C, --cwd PATH            Change the working directory before running the utility. Overrides DEFAULT_CWD.
  tag / commit / sync
    -m, --message MSG         Template for tag/commit message. Use {zid} to dynamically inject the generated ZID.
    -l, --log-file PATHS...   One or more paths to history log files to record sync snapshots (defaults to DEFAULT_LOG_PATHS).
    --log-format FORMAT       Logging format (choices: table, code, log). Overrides LOG_FORMAT.
  tag / sync
    -f, --force               Force tag creation without confirmation on dirty worktrees.
    -p, --push                Push tags to remote origin repository.
  checkout
    -f, --force               Force checkout (discarding local changes).
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("-C", "--cwd", default=DEFAULT_CWD, metavar="<path>", help="Change the working directory before running the utility (default: DEFAULT_CWD or shell's current directory).")
    subparsers = parser.add_subparsers(dest="command", required=True, title="commands", metavar="<command>")
    
    # status subcommand
    subparsers.add_parser("status", help="Show current branch, status, and tags across repositories.")
    
    # tag subcommand
    parser_tag = subparsers.add_parser("tag", help="Create a coordinated tag across all repositories.")
    parser_tag.add_argument("name", nargs="?", help="Tag name. Defaults to current ZID if omitted.")
    parser_tag.add_argument("-f", "--force", action="store_true", help="Force tag creation without confirmation on dirty worktrees.")
    parser_tag.add_argument("-m", "--message", help="Tag message template. Use {zid} to insert ZID.")
    parser_tag.add_argument("-l", "--log-file", nargs="+", help="One or more paths to history log files to record sync snapshots (defaults to DEFAULT_LOG_PATHS).")
    parser_tag.add_argument("-p", "--push", action="store_true", help="Push tags to remote origin repository.")
    parser_tag.add_argument("--log-format", choices=["table", "code", "log"], default=None, help="Logging format. Overrides LOG_FORMAT.")
    
    # commit subcommand
    parser_commit = subparsers.add_parser("commit", help="Commit dirty repositories sequentially with unique ZIDs.")
    parser_commit.add_argument("-m", "--message", help="Commit message template. Use {zid} to insert ZID.")
    parser_commit.add_argument("-l", "--log-file", nargs="+", help="One or more paths to history log files to record post-commit hashes (defaults to DEFAULT_LOG_PATHS).")
    parser_commit.add_argument("--log-format", choices=["table", "code", "log"], default=None, help="Logging format. Overrides LOG_FORMAT.")
    
    # checkout subcommand
    parser_checkout = subparsers.add_parser("checkout", help="Checkout a specific tag/branch across all repositories.")
    parser_checkout.add_argument("name", help="Tag or branch name to checkout.")
    parser_checkout.add_argument("-f", "--force", action="store_true", help="Force checkout (discarding local changes).")
    
    # sync subcommand
    parser_sync = subparsers.add_parser("sync", help="Commit dirty repositories and immediately tag them.")
    parser_sync.add_argument("name", nargs="?", help="Tag name. Defaults to current ZID if omitted.")
    parser_sync.add_argument("-f", "--force", action="store_true", help="Force tag creation without confirmation on dirty worktrees.")
    parser_sync.add_argument("-m", "--message", help="Tag/commit message template. Use {zid} to insert ZID.")
    parser_sync.add_argument("-l", "--log-file", nargs="+", help="One or more paths to history log files to record sync snapshots (defaults to DEFAULT_LOG_PATHS).")
    parser_sync.add_argument("-p", "--push", action="store_true", help="Push tags to remote origin repository.")
    parser_sync.add_argument("--log-format", choices=["table", "code", "log"], default=None, help="Logging format. Overrides LOG_FORMAT.")
    
    # delete subcommand
    parser_delete = subparsers.add_parser("delete", help="Delete a specific tag across all repositories.")
    parser_delete.add_argument("name", help="Tag name to delete.")
    
    args = parser.parse_args()
    
    # Resolve default log files if omitted from CLI
    if hasattr(args, "log_file") and not args.log_file:
        args.log_file = DEFAULT_LOG_PATHS
        
    if args.cwd:
        try:
            os.chdir(args.cwd)
        except Exception as e:
            print(f"sync: Error - Could not change working directory to '{args.cwd}' ({e})")
            sys.exit(1)
            
    
    if args.command == "status":
        cmd_status(args)
    elif args.command == "tag":
        cmd_tag(args)
    elif args.command == "commit":
        cmd_commit(args)
    elif args.command == "checkout":
        cmd_checkout(args)
    elif args.command == "sync":
        cmd_sync(args)
    elif args.command == "delete":
        cmd_delete(args)

if __name__ == "__main__":
    main()
