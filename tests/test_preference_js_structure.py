"""Conservative branch ceilings for the small, compiler-free preference slice."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]


def function_body(source, name):
    # These functions contain no regex/template literals. Remove strings/comments
    # before balancing braces, then count all nested decisions as an upper bound.
    match = re.search(r'function\s+'+name+r'\([^)]*\)\s*\{', source)
    assert match, name
    source = source[match.end():]
    source = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//[^\n]*|/\*[\s\S]*?\*/', '""', source)
    start = 0
    depth = 1
    for index in range(start, len(source)):
        depth += (source[index] == '{') - (source[index] == '}')
        if not depth:
            return source[start:index]
    raise AssertionError('Unbalanced function: '+name)


def test_preference_javascript_branch_upper_bound():
    sources = {
        ROOT/'web/js/features/preferences.js': ['create', 'settle', 'submit', 'flushSlots', 'current', 'unresolved'],
        ROOT/'web/js/features/sqlite-state.js': ['restoreOccurrences', 'clientSlot', 'legacyKeys', 'clearLegacy',
            'readLegacy', 'legacyBoolean', 'importDialog', 'finish', 'importPreview', 'viewBody', 'readView',
            'migrate', 'start', 'reportFailure', 'save', 'flush', 'reconcileFailure', 'sendPreference', 'receive', 'create',
            'requireView', 'requirePreferenceAck'],
        ROOT/'web/js/app.js': ['projectPreferenceFeedback', 'projectPreferenceStatus',
            'sendPreference', 'persistPreference', 'projectPreferenceClass',
            'setHighlightSameLemma', 'setFilterSelectedOnly', 'stateRequest', 'captureClientView', 'restoreClientView'],
    }
    measured = {}
    for path, names in sources.items():
        source = path.read_text(encoding='utf-8')
        for name in names:
            body = function_body(source, name)
            measured[name] = 1+len(re.findall(r'\b(?:if|for|while|case|catch)\b|&&|\|\||\?', body))
    assert max(measured.values()) <= 7, measured
