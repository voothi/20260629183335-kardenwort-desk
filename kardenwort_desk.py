import sys
import argparse
import json
import logging
from datetime import datetime

# Setup structured logging
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
        }
        if hasattr(record, "zid"):
            log_data["zid"] = record.zid
        return json.dumps(log_data)

logger = logging.getLogger("kardenwort_desk")

def setup_logging(verbose=False, debug=False):
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    if debug:
        logger.setLevel(logging.DEBUG)
    elif verbose:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.WARNING)

def print_structured_error(error_code, message, details=None):
    error_payload = {
        "error_code": error_code,
        "message": message,
    }
    if details:
        error_payload["details"] = details
    sys.stderr.write(json.dumps(error_payload) + "\n")

def cmd_render(args):
    logger.info("Render subcommand invoked", extra={"zid": args.zid})
    print("RENDER SKELETON OUTPUT")

def cmd_export(args):
    logger.info("Export subcommand invoked")
    print("EXPORT SKELETON OUTPUT")

def cmd_edit_save(args):
    logger.info("Edit-save subcommand invoked", extra={"zid": args.zid})
    print("EDIT-SAVE SKELETON OUTPUT")

def cmd_merge(args):
    logger.info("Merge subcommand invoked")
    print("MERGE SKELETON OUTPUT")

def cmd_restore(args):
    logger.info("Restore subcommand invoked")
    print("RESTORE SKELETON OUTPUT")

def cmd_desk(args):
    logger.info("Desk subcommand invoked")
    print("DESK SKELETON OUTPUT")

def main():
    parser = argparse.ArgumentParser(description="Kardenwort Desk Orchestration Core")
    parser.add_argument("--config", default="config.ini", help="Path to config.ini")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--debug", action="store_true", help="Debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # render
    p_render = subparsers.add_parser("render")
    p_render.add_argument("--text", help="Selected text")
    p_render.add_argument("--language", required=True, help="Language code")
    p_render.add_argument("--zid", required=True, help="Session ZID")
    p_render.add_argument("--text-mode", choices=["single", "multi"], default="single")

    # export
    p_export = subparsers.add_parser("export")
    p_export.add_argument("--selection-manifest", required=True, help="Selection manifest path")
    p_export.add_argument("--language", required=True, help="Language code")

    # edit-save
    p_edit = subparsers.add_parser("edit-save")
    p_edit.add_argument("--deltas", required=True, help="Deltas JSON file path")
    p_edit.add_argument("--zid", required=True, help="Session ZID")
    p_edit.add_argument("--language", help="Language code")

    # merge
    p_merge = subparsers.add_parser("merge")
    p_merge.add_argument("--files", nargs="+", required=True, help="List of TSV files to merge")
    p_merge.add_argument("--target", default="new", help="Merge target path, new, or first")

    # restore
    p_restore = subparsers.add_parser("restore")
    p_restore.add_argument("--file", required=True, help="Session file to restore")

    # desk
    p_desk = subparsers.add_parser("desk")
    p_desk.add_argument("--file", required=True, help="Text file to analyze")
    p_desk.add_argument("--text-mode", choices=["single", "multi"], default="multi")
    p_desk.add_argument("--language", help="Language code")

    try:
        args = parser.parse_args()
    except SystemExit as e:
        if e.code != 0:
            print_structured_error("INVALID_ARGS", "Failed to parse command line arguments")
            sys.exit(1)
        sys.exit(0)

    setup_logging(verbose=args.verbose, debug=args.debug)

    commands = {
        "render": cmd_render,
        "export": cmd_export,
        "edit-save": cmd_edit_save,
        "merge": cmd_merge,
        "restore": cmd_restore,
        "desk": cmd_desk,
    }

    try:
        commands[args.command](args)
    except Exception as e:
        print_structured_error("COMMAND_FAILED", str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
