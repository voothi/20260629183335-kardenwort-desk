"""Pure exact identity contract for client-scoped reload state."""
import re


def client_identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{32}', value):
        raise ValueError('Invalid client slot')
    return value


def occurrence_identity(item):
    if not isinstance(item, dict):
        raise ValueError('Focus must contain occurrence descriptors')
    sentence, token = item.get('sentence_idx'), item.get('token_order')
    if isinstance(sentence, bool) or isinstance(token, bool):
        raise ValueError('Invalid occurrence identity')
    if not str(sentence).isdigit() or not str(token).isdigit():
        raise ValueError('Missing exact occurrence identity')
    if int(sentence) < 1:
        raise ValueError('Invalid sentence identity')
    return int(sentence), int(token)
