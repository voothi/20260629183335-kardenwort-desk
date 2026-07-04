# Release Notes - v1.0.0 (Dynamic Numbered Cascades & Tray Isolation)

**Date**: 2026-07-04
**Version**: v1.0.0
**Implementation ZIDs**: 20260704214903, 20260704215806, 20260704220313, 20260704220615, 20260704220756, 20260704221126, 20260704221351, 20260704221555, 20260704222150, 20260704223119, 20260704225237, 20260704230805, 20260704230916, 20260704231044, 20260704231117, 20260704231232

## Highlights

### 🔢 **Dynamic Window & Taskbar Numbering Sequence**
- **Cascade Launch Tracking**: Introduced Mutex-synchronized process coordination via registry counters to assign sequential IDs (1, 2, 3...) during cascade-opening (e.g., via "Send to...").
- **Taskbar Icon Grouping Override**: Injected Win32 `SetCurrentProcessExplicitAppUserModelID` API calls to isolate window groups and display distinct sequence numbers on the Taskbar for each concurrent window.
- **Title Bar Integration**: Automatically appends the window's sequence number to the title bar.

### 🎨 **Seamless Tray Icon Isolation**
- **Independent System Tray**: Isolated window-specific numbering from the global system tray. The tray icon retains the language status indicator (En/De) unmodified.
- **Explicit Window Icons**: Replaced generic GUI icon commands with direct `WM_SETICON` Win32 messages (`ICON_SMALL` & `ICON_BIG` via `SendMessage`) using `LoadPicture` on dynamically loaded `.ico` files.

### ⚙️ **Centering Optimization & High-Quality Assets**
- **GDI+ GenericTypographic Centering**: Rewrote the icon generator script to run in `GenericTypographic` mode, stripping GDI+ character-bounding box padding. 
- **Adaptive Sizing**: Restored the larger `16pt` font size for single digits (1-9) to fill the circle area, while retaining the scaled `11pt` font for double digits (10+) to prevent clipping. 

### 📖 **Documentation & Ecosystem Alignment**
- **Visual Showcase & Assets**: Created a Visual Showcase section in the `README` with detailed user flow screenshots copied directly into the project's `assets/` directory.
- **Table of Contents & Navigator Links**: Re-architected the `README` to align with the `kardenwort-mpv` style, including an expanded multi-level Table of Contents and `[Return to Top](#table-of-contents)` scroll-links.
- **Project Structure & Sibling Dependencies**: Added a file tree breakdown and documented sibling relationships with other Kardenwort ecosystem projects (Parser, IntelliFiller, AHK Frontend, Deep-Translator).
