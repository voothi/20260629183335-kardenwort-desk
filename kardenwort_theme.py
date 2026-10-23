"""Pure configuration-to-color contract for the shared web shell."""

import re


# Keys recovered from release 56f8696. Aliases reach both modern and legacy CSS.
THEME_COLOR_PROPERTIES = {
    'bg_color': ('bg-color', 'bg-primary'),
    'text_color': ('text-color', 'text-main'),
    'section_bg': ('section-bg', 'bg-secondary'),
    'section_border': ('section-border', 'border-color'),
    'text_muted': ('text-muted', 'skeleton-text'),
    'table_border': ('table-border', 'border-subtle'),
    'table_th_border': ('table-th-border',),
    'table_text': ('table-text',),
    'row_hover': ('row-hover', 'bg-hover'),
    'word_hover': ('word-hover', 'configured-word-hover'),
    'highlight_orange_active_bg': ('highlight-orange-bg',),
    'highlight_orange_active_text': ('highlight-orange-text',),
    'highlight_orange_active_hover_bg': ('highlight-orange-hover-bg',),
    'highlight_purple_active_bg': ('highlight-purple-bg',),
    'highlight_purple_active_text': ('highlight-purple-text',),
    'highlight_purple_active_hover_bg': ('highlight-purple-hover-bg',),
    'selected_orange_row_bg': ('selected-orange-row-bg', 'selected-row-bg'),
    'selected_orange_row_text': ('selected-orange-row-text',),
    'selected_purple_row_bg': ('selected-purple-row-bg',),
    'selected_purple_row_text': ('selected-purple-row-text',),
    'flipped_bg': ('flipped-bg',),
    'flipped_text': ('flipped-text',),
    'flipped_border': ('flipped-border',),
    'input_bg': ('input-bg', 'bg-card'),
    'input_border': ('input-border',),
    'scrollbar_track': ('scrollbar-track',),
    'scrollbar_thumb': ('scrollbar-thumb',),
    'scrollbar_thumb_hover': ('scrollbar-thumb-hover',),
    'not_connected_bg': ('not-connected-bg',),
    'not_connected_text': ('not-connected-text',),
    'level_3k_color': ('level-3k-color',),
    'level_5k_color': ('level-5k-color',),
    'level_goethe_color': ('level-goethe-color',),
    'modal_bg': ('modal-bg',),
    'modal_border': ('modal-border',),
    'toolbar_bg': ('toolbar-bg',),
    'toolbar_btn_hover': ('toolbar-btn-hover',),
}

_HEX_COLOR = re.compile(r'#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})', re.I)
_FUNCTION_COLOR = re.compile(r'(?:rgb|hsl)a?\(\s*[+-]?[\d.]+%?(?:\s*,\s*[+-]?[\d.]+%?){2,3}\s*\)', re.I)
_NAMED_COLORS = frozenset('transparent currentcolor black white red green blue gray grey yellow orange purple pink brown cyan magenta navy teal lime olive maroon silver aqua fuchsia rebeccapurple'.split())


def validate_color(value):
    value = value.strip()
    if not (_HEX_COLOR.fullmatch(value) or _FUNCTION_COLOR.fullmatch(value) or value.lower() in _NAMED_COLORS):
        raise ValueError('Invalid configured theme color')
    return value


def resolve_theme_colors(config, theme):
    if theme not in ('dark', 'light', 'white'):
        return {}
    section = 'theme_light' if theme in ('light', 'white') else 'theme_dark'
    if not config.has_section(section):
        return {}
    colors = {}
    for key, properties in THEME_COLOR_PROPERTIES.items():
        if config.has_option(section, key):
            value = validate_color(config.get(section, key))
            colors.update((name, value) for name in properties)
    return colors


def theme_style(colors, theme):
    if colors and theme not in ('dark', 'light', 'white'):
        raise ValueError('Invalid configured theme')
    allowed = {name for properties in THEME_COLOR_PROPERTIES.values() for name in properties}
    if not set(colors).issubset(allowed):
        raise ValueError('Unknown configured theme property')
    declarations = ''.join(f'--{key}:{validate_color(value)};' for key, value in colors.items())
    return f'<style id="configured-theme">body.theme-{theme}{{{declarations}}}</style>' if declarations else ''
