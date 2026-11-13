"""Owned static inputs for ordinary pytest; application parsing stays real."""
import configparser
from pathlib import Path

from scripts.release_audit_sources import scrub_credentials


class DefaultConfiguration:
    def __init__(self, root, owner, loader):
        self.root, self.owner, self.loader = root, owner, loader

    def __call__(self, config_path=None):
        requested = Path(config_path).resolve() if config_path is not None else self.root/'config.ini'
        if requested != self.root/'config.ini':
            return self.loader(config_path)
        destination = self.owner/'config.ini'
        if not destination.exists():
            write_default_configuration(self.root, self.owner)
        return self.loader(destination)


def write_default_configuration(root, owner):
    owner.mkdir(parents=True, exist_ok=True)
    config = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    config.read(root/'config.ini', encoding='utf-8-sig')
    scrub_credentials(config)
    for key, value in config['environment'].items():
        config.set('environment', key, str((root/value).resolve()))
    settings = owner/'provider-settings.ini'
    settings.write_text('[Security]\n', encoding='utf-8')
    config.set('environment', 'deepl_settings_file', str(settings))
    config.read_dict({
        'storage': {'sqlite_db_path': str(owner/'fixture.db')},
        'project_structure': {'generated_results_dir': str(owner/'results')},
        'settings': {'anki_mapping_file': str(root/'anki-mapping.ini'),
                     'favorites_output_dir': str(owner/'favorites')}})
    with (owner/'config.ini').open('w', encoding='utf-8') as stream:
        config.write(stream)
