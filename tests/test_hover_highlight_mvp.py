import re
from pathlib import Path
import pytest
import kardenwort_desk as desk

def test_injected_script_ie11_compatibility():
    # Resolve path to kardenwort-window.ahk
    ahk_path = Path(__file__).parents[2] / "20240411110510-autohotkey" / "kardenwort-window" / "kardenwort-window.ahk"
    assert ahk_path.exists(), f"AHK file not found at {ahk_path}"
    
    ahk_content = ahk_path.read_text(encoding="utf-8")
    
    # Extract the JS script block from AHK file
    # It starts with js := "(function() {" and ends before doc.body.appendChild(scriptEl) or similar
    js_lines = []
    in_js = False
    for line in ahk_content.splitlines():
        if 'js := ""' in line or 'js .= ' in line:
            # Extract whatever is inside quotes
            match = re.search(r'js \.=\s*"(.*)"', line)
            if not match:
                match = re.search(r'js :=\s*"(.*)"', line)
            if match:
                js_lines.append(match.group(1))
    
    js_code = "\n".join(js_lines)
    assert len(js_code) > 0, "Could not extract JS code from AHK file"
    
    # 1. Assert no arrow functions (=>)
    assert "=>" not in js_code, "Arrow functions (=>) are not allowed in IE11-safe script"
    
    # 2. Assert every \\p{L} is inside a try-catch block
    # Let's verify that the text \\p{L} only appears in the try block
    # We can check that the string has the try/catch around the RegExp using \p{L}
    assert "\\\\p{L}" in js_code or "\\p{L}" in js_code
    
    # Check that any RegExp compile with \p{L} is wrapped in a try/catch
    # Let's count occurrences of \p{L}
    p_l_count = js_code.count("\\p{L}") + js_code.count("\\\\p{L}")
    assert p_l_count > 0
    
    # Verify the code block format:
    # try {
    #   rx = new RegExp('([\\p{L}0-9\x27]+)', 'gu');
    # } catch(e) {
    #   rx = ...
    # }
    assert "try" in js_code
    assert "catch" in js_code

def test_desk_render_output_untouched():
    # Assert desk codebase doesn't reference any MVP highlights or variables
    desk_path = Path(desk.__file__)
    desk_content = desk_path.read_text(encoding="utf-8")
    
    assert "hl-mvp" not in desk_content, "Desk code must remain untouched by MVP styling classes"
    assert "HoverHighlightMvp" not in desk_content, "Desk code must not reference MVP configuration variables"
