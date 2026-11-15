"""Ephemeral configuration transport for an owned detached worker."""
import configparser
import json
import os
from pathlib import Path

INTERPOLATIONS = {kind.__name__: kind for kind in (
    configparser.Interpolation, configparser.BasicInterpolation, configparser.ExtendedInterpolation)}


def encode_runtime(config, paths):
    if config is None:
        raise ValueError('Detached progressive launch requires caller configuration')
    values = {key: value for key, value in paths.items() if key != 'config'}
    path_keys = [key for key, value in values.items() if isinstance(value, os.PathLike)]
    payload = {
        'defaults': dict(config.defaults()),
        'case_sensitive': config.optionxform('A') == 'A',
        'interpolation': INTERPOLATIONS[type(config._interpolation).__name__].__name__,
        'sections': {section: dict(config.items(section, raw=True)) for section in config.sections()},
        'paths': values, 'path_keys': path_keys,
    }
    return json.dumps(payload, default=os.fspath).encode('utf-8')


def decode_runtime(raw):
    payload = json.loads(raw)
    config = configparser.ConfigParser(allow_no_value=True,
        interpolation=INTERPOLATIONS[payload['interpolation']]())
    if payload['case_sensitive']:
        config.optionxform = str
    config.read_dict({'DEFAULT': payload['defaults'], **payload['sections']})
    paths = payload['paths']
    for key in payload['path_keys']:
        paths[key] = Path(paths[key])
    return config, paths
