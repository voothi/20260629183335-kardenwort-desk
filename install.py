import os
import sys
import subprocess
import argparse

SENDTO_DIRECTORY = r"%APPDATA%\Microsoft\Windows\SendTo"

# Registry of entrypoints
# Format: { "Shortcut Name": { "arguments": "...", "desc": "description" } }
ENTRYPOINTS = {
    "Kardenwort Merge": { "arguments": "merge --deduplicate --sort-frequency --files", "desc": "Merge multiple kardenwort TSVs" },
    "Kardenwort Desk": { "arguments": "desk --file", "desc": "Open text file or restore session in desk window" }
}

def create_shortcut(name, arguments, description):
    sendto_dir = os.path.expandvars(SENDTO_DIRECTORY)
    os.makedirs(sendto_dir, exist_ok=True)
    shortcut_path = os.path.join(sendto_dir, f"{name}.lnk")

    python_path = sys.executable
    if python_path.lower().endswith("pythonw.exe"):
        python_path = python_path[:-len("pythonw.exe")] + "python.exe"

    current_dir = os.path.dirname(os.path.abspath(__file__))
    script_path = os.path.join(current_dir, "kardenwort_desk.py")

    shortcut_path_escaped = shortcut_path.replace("'", "''")
    python_path_escaped = python_path.replace("'", "''")
    script_path_escaped = script_path.replace("'", "''")

    ps_script = (
        f"$WshShell = New-Object -ComObject WScript.Shell; "
        f"$Shortcut = $WshShell.CreateShortcut('{shortcut_path_escaped}'); "
        f"$Shortcut.TargetPath = '{python_path_escaped}'; "
        f"$Shortcut.Arguments = '\"{script_path_escaped}\" {arguments}'; "
        f"$Shortcut.Description = '{description}'; "
        f"$Shortcut.WindowStyle = 1; "   # SW_SHOWNORMAL
        f"$Shortcut.Save()"
    )

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True,
            text=True,
            check=True,
        )
        print(f"SUCCESS: Created shortcut '{name}'")
    except subprocess.CalledProcessError as exc:
        print(f"Error: Failed to create shortcut '{name}'.\nPowerShell error:\n{exc.stderr}")

def main():
    parser = argparse.ArgumentParser(description="Kardenwort Desk SendTo Shortcuts Installer")
    parser.add_argument("--list", action="store_true", help="List all registered shortcuts")
    parser.add_argument("--uninstall", action="store_true", help="Uninstall all registered shortcuts")
    args = parser.parse_args()

    if args.list:
        print("Registered SendTo entrypoints:")
        if not ENTRYPOINTS:
            print("  (No shortcuts registered yet)")
        for name, info in ENTRYPOINTS.items():
            print(f"  - {name}: {info['desc']}")
        return

    if args.uninstall:
        sendto_dir = os.path.expandvars(SENDTO_DIRECTORY)
        for name in ENTRYPOINTS.keys():
            shortcut_path = os.path.join(sendto_dir, f"{name}.lnk")
            if os.path.exists(shortcut_path):
                os.remove(shortcut_path)
                print(f"Removed shortcut '{name}'")
        return

    # Install action (default)
    print("Installing Kardenwort Desk shortcuts...")
    if not ENTRYPOINTS:
        print("  No entrypoints defined to install in the registry skeleton yet.")
    for name, info in ENTRYPOINTS.items():
        arguments = info["arguments"]
        desc = info["desc"]
        create_shortcut(name, arguments, desc)

if __name__ == "__main__":
    main()
