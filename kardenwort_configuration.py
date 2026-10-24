"""Pure line-preserving INI preference patches."""
import re


def section_bounds(lines, section):
    start = None
    for index, line in enumerate(lines):
        match = re.match(r'^\s*\[([^\]]+)\]\s*$', line.lstrip('\ufeff'))
        if not match:
            continue
        if start is not None:
            return start, index
        if match.group(1).strip().lower() == section.lower():
            start = index + 1
    return start, len(lines)


def insert_ini_line(lines, index, line, newline):
    prefix = ''.join(lines[:index])
    if prefix and not prefix.endswith(('\n', '\r')):
        prefix += newline
    return prefix + line + newline + ''.join(lines[index:])


def replacement_line(match, value):
    return match.group(1) + value + (match.group(2) or '')


def patch_ini_value(content, section, key, value):
    if '\n' in value or '\r' in value:
        raise ValueError('INI preference values must occupy one line')
    lines = content.splitlines(keepends=True)
    newline_match = re.search(r'\r?\n', content)
    newline = newline_match.group() if newline_match else '\n'
    start, end = section_bounds(lines, section)
    if start is None:
        return insert_ini_line(lines, end, f'[{section}]{newline}{key} = {value}', newline)
    pattern = re.compile(r'^([ \t]*' + re.escape(key) + r'[ \t]*=[ \t]*)[^\r\n]*(\r?\n)?$', re.IGNORECASE)
    for index in range(start, end):
        match = pattern.match(lines[index])
        if match:
            lines[index] = replacement_line(match, value)
            return ''.join(lines)
    return insert_ini_line(lines, end, f'{key} = {value}', newline)
