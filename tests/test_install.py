import pytest
import sys
import install

def test_install_entrypoints_structure():
    assert "Kardenwort Open Desk" in install.ENTRYPOINTS
    assert "Kardenwort Merge Files" in install.ENTRYPOINTS
    
    for name, info in install.ENTRYPOINTS.items():
        assert "arguments" in info
        assert "desc" in info

def test_install_list_option(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["install.py", "--list"])
    install.main()
    captured = capsys.readouterr()
    assert "Registered SendTo entrypoints:" in captured.out
    assert "Kardenwort Open Desk" in captured.out
