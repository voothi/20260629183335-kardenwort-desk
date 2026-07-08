import sys
import argparse
import json
import logging
import configparser
import os
import re
import subprocess
import tempfile
import shutil
import contextlib
import html
import socket
import concurrent.futures
from pathlib import Path
from datetime import datetime, timezone

import text_tokenizer as tok


class ConfigError(Exception):
    pass

def parse_sections_list(raw, valid_tokens):
    if not raw or not raw.strip():
        return []
    result = []
    for token in raw.split(','):
        t = token.strip()
        if not t:
            continue
        if t in valid_tokens:
            result.append(t)
        else:
            sys.stderr.write(f"Warning: Unknown section token '{t}' ignored.\n")
    return result

def parse_columns_list(raw, valid_tokens):
    if not raw or not raw.strip():
        return []
    result = []
    for token in raw.split(','):
        t = token.strip()
        if not t:
            continue
        if t in valid_tokens:
            result.append(t)
        else:
            sys.stderr.write(f"Warning: Unknown column token '{t}' ignored.\n")
    return result

_warned_keys = set()
def _warn_deprecated(key, msg):
    if key not in _warned_keys:
        _warned_keys.add(key)
        logger.warning(msg)

def _migrate_config(config):
    # Ensure sections exist
    if not config.has_section('pipeline'):
        config.add_section('pipeline')
    if not config.has_section('triggers'):
        config.add_section('triggers')
    if not config.has_section('rendering'):
        config.add_section('rendering')

    # Read legacy providers
    legacy_main = config.get('translation_providers', 'main_text_translation', fallback=None) if config.has_section('translation_providers') else None
    legacy_lemmas = config.get('translation_providers', 'lemmas_translation', fallback=None) if config.has_section('translation_providers') else None

    # Resolve text_base_provider (migrated from main_text_provider or legacy_main)
    text_base = config.get('pipeline', 'text_base_provider', fallback=None)
    old_main_text = config.get('pipeline', 'main_text_provider', fallback=None)
    if text_base is None:
        if old_main_text is not None:
            text_base = old_main_text
        elif legacy_main is not None:
            text_base = legacy_main
        else:
            text_base = 'google'
    config.set('pipeline', 'text_base_provider', text_base)

    # Resolve text_reprocess_provider
    text_reprocess = config.get('pipeline', 'text_reprocess_provider', fallback=None)
    if text_reprocess is None:
        text_reprocess = 'deepl'
    config.set('pipeline', 'text_reprocess_provider', text_reprocess)

    # Resolve lemma_base_provider (migrated from base_provider)
    lemma_base = config.get('pipeline', 'lemma_base_provider', fallback=None)
    old_base = config.get('pipeline', 'base_provider', fallback=None)
    if lemma_base is None:
        if old_base is not None:
            lemma_base = old_base
        else:
            if legacy_main is not None or legacy_lemmas is not None:
                _warn_deprecated('translation_providers', "Section [translation_providers] is deprecated; map its settings to [pipeline].")
            if legacy_main == 'deepl':
                lemma_base = 'deepl'
            else:
                lemma_base = 'google'
    config.set('pipeline', 'lemma_base_provider', lemma_base)

    # Resolve lemma_reprocess_provider (migrated from enrichment_provider)
    lemma_reprocess = config.get('pipeline', 'lemma_reprocess_provider', fallback=None)
    old_enrichment = config.get('pipeline', 'enrichment_provider', fallback=None)
    if lemma_reprocess is None:
        if old_enrichment is not None:
            lemma_reprocess = old_enrichment
        else:
            if legacy_main is not None or legacy_lemmas is not None:
                _warn_deprecated('translation_providers', "Section [translation_providers] is deprecated; map its settings to [pipeline].")
            if legacy_lemmas in ('google', 'deepl'):
                lemma_reprocess = 'none'
            elif legacy_lemmas in ('intellifiller', 'combined'):
                lemma_reprocess = 'intellifiller'
            else:
                lemma_reprocess = 'intellifiller'
    config.set('pipeline', 'lemma_reprocess_provider', lemma_reprocess)

    # Read legacy triggers
    legacy_lazy = config.get('settings', 'lazy_processing', fallback=None) if config.has_section('settings') else None

    # Resolve triggers
    run_lemma_base = config.get('triggers', 'run_lemma_base_translation', fallback=None)
    old_run_base = config.get('triggers', 'run_base_translation', fallback=None)
    run_lemma_enrich = config.get('triggers', 'run_lemma_enrichment', fallback=None)
    old_run_enrich = config.get('triggers', 'run_enrichment', fallback=None)
    run_text = config.get('triggers', 'run_text_translation', fallback=None)

    if run_lemma_base is None or run_lemma_enrich is None or run_text is None:
        mapped_base = 'auto'
        mapped_enrich = 'auto'
        if legacy_lazy is not None:
            _warn_deprecated('lazy_processing', "lazy_processing is deprecated; map it to triggers.run_lemma_base_translation and triggers.run_lemma_enrichment.")
            lazy_val = legacy_lazy.lower()
            if lazy_val in ('true', 'all'):
                mapped_base = 'manual'
                mapped_enrich = 'manual'
            elif lazy_val == 'llm_only':
                mapped_base = 'auto'
                mapped_enrich = 'manual'
            else:
                mapped_base = 'auto'
                mapped_enrich = 'auto'
        
        if run_lemma_base is None:
            run_lemma_base = old_run_base if old_run_base is not None else mapped_base
        if run_lemma_enrich is None:
            run_lemma_enrich = old_run_enrich if old_run_enrich is not None else mapped_enrich
        if run_text is None:
            run_text = run_lemma_base
    
    config.set('triggers', 'run_lemma_base_translation', run_lemma_base)
    config.set('triggers', 'run_lemma_enrichment', run_lemma_enrich)
    config.set('triggers', 'run_text_translation', run_text)

    # Read legacy rendering
    legacy_prog = config.get('settings', 'progressive_loading', fallback=None) if config.has_section('settings') else None

    # Resolve rendering
    display_mode = config.get('rendering', 'display_mode', fallback=None)
    if display_mode is None:
        if legacy_prog is not None:
            _warn_deprecated('progressive_loading', "progressive_loading is deprecated; map it to rendering.display_mode.")
            if legacy_prog.lower() == 'true':
                display_mode = 'progressive'
            else:
                display_mode = 'monolithic'
        else:
            display_mode = 'progressive'
    config.set('rendering', 'display_mode', display_mode)

    if not config.has_section('settings'):
        config.add_section('settings')
    raw_gap = config.get('settings', 'split_gap_limit', fallback=None)
    val_gap = 60
    if raw_gap is not None:
        try:
            val_gap = int(raw_gap)
        except ValueError:
            val_gap = 60
    config.set('settings', 'split_gap_limit', str(val_gap))

def load_config(config_path=None):
    """
    Loads config.ini.
    Resolves relative paths starting with '../' or './' relative to the config file's location.
    Validates that all environment paths exist.
    """
    if config_path is None:
        config_path = Path(__file__).resolve().parent / "config.ini"
    else:
        config_path = Path(config_path).resolve()
        
    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")
        
    config = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    config.read(config_path, encoding='utf-8')
    
    base_dir = config_path.parent
    resolved_paths = {'base_dir': base_dir}
    
    # 1. Resolve environment paths
    if 'environment' in config:
        for key, value in config['environment'].items():
            if value.startswith('../') or value.startswith('./'):
                resolved_path = (base_dir / value).resolve()
            else:
                resolved_path = Path(value).resolve()
            resolved_paths[key] = resolved_path
            
    # Check each resolved path exists
    for key, path in resolved_paths.items():
        if key == 'base_dir':
            continue
        if not path.exists():
            raise ConfigError(f"Path configured for '{key}' does not exist: {path}")
            
    # 2. Settings paths
    if 'settings' in config:
        # favorites_output_dir is relative to config.ini location
        fav_dir = config['settings'].get('favorites_output_dir', './favorites')
        if fav_dir.startswith('../') or fav_dir.startswith('./'):
            resolved_paths['favorites_output_dir'] = (base_dir / fav_dir).resolve()
        else:
            resolved_paths['favorites_output_dir'] = Path(fav_dir).resolve()
            
        # anki_mapping_file is relative to config.ini location
        mapping_file = config['settings'].get('anki_mapping_file', './anki-mapping.ini')
        if mapping_file.startswith('../') or mapping_file.startswith('./'):
            resolved_paths['anki_mapping_file'] = (base_dir / mapping_file).resolve()
        else:
            resolved_paths['anki_mapping_file'] = Path(mapping_file).resolve()
            
        if not resolved_paths['anki_mapping_file'].exists():
            raise ConfigError(f"anki_mapping_file path configured for 'anki_mapping_file' does not exist: {resolved_paths['anki_mapping_file']}")
            
    goldendict = {}
    if 'goldendict' in config:
        gd = config['goldendict']
        goldendict['format'] = gd.get('format', 'html')
        goldendict['target_language'] = gd.get('target_language', config.get('settings', 'default_target_language', fallback='ru'))
        if not goldendict['target_language']:
            goldendict['target_language'] = config.get('settings', 'default_target_language', fallback='ru')
        goldendict['run_intellifiller'] = gd.getboolean('run_intellifiller', fallback=False)
        goldendict['lookup_ttl_seconds'] = gd.getint('lookup_ttl_seconds', fallback=300)
        goldendict['theme'] = gd.get('theme', 'dark')
        goldendict['emit_meta_comment'] = gd.getboolean('emit_meta_comment', fallback=True)
        goldendict['disable_css'] = gd.getboolean('disable_css', fallback=False)
        
        raw_sections = gd.get('sections', 'translation,lemmas')
        goldendict['sections'] = parse_sections_list(raw_sections, ['source', 'translation', 'lemmas'])
        
        goldendict['heading_source'] = gd.get('heading_source', '')
        goldendict['heading_translation'] = gd.get('heading_translation', '')
        goldendict['heading_lemmas'] = gd.get('heading_lemmas', '')
        
        raw_columns = gd.get('lemma_columns', 'inflected,lemma,translation')
        goldendict['lemma_columns'] = parse_columns_list(raw_columns, ['inflected', 'lemma', 'ipa', 'morphology', 'translation'])
    else:
        goldendict['format'] = 'html'
        goldendict['target_language'] = config.get('settings', 'default_target_language', fallback='ru')
        goldendict['run_intellifiller'] = False
        goldendict['lookup_ttl_seconds'] = 300
        goldendict['theme'] = 'dark'
        goldendict['emit_meta_comment'] = True
        goldendict['disable_css'] = False
        goldendict['sections'] = ['translation', 'lemmas']
        goldendict['heading_source'] = ''
        goldendict['heading_translation'] = ''
        goldendict['heading_lemmas'] = ''
        goldendict['lemma_columns'] = ['inflected', 'lemma', 'translation']

    _migrate_config(config)
    _validate_translation_config(config)
    return config, resolved_paths, goldendict

def load_kardenwort_config(kardenwort_workspace):
    kw_config = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    kw_config.read(kardenwort_workspace / "config.ini", encoding='utf-8')
    return kw_config

def load_anki_mapping(mapping_path):
    mapping = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    mapping.optionxform = str # Preserve case for Anki field names!
    mapping.read(mapping_path, encoding='utf-8')
    return mapping

def build_field_mapping(mapping, mode):
    field_mapping = dict(mapping[f'fields_mapping.{mode}'])
    if 'tts' in mapping:
        field_mapping.update(dict(mapping['tts']))
    return field_mapping

def get_role_fields(mapping, headers):
    role_fields = {role: field for field, role in mapping['desk_columns'].items() if field in headers} if 'desk_columns' in mapping else {}
    if 'WordSourceMorphologyAI' in headers and 'morphology' not in role_fields:
        role_fields['morphology'] = 'WordSourceMorphologyAI'
    if 'WordSourceIPA' in headers and 'ipa' not in role_fields:
        role_fields['ipa'] = 'WordSourceIPA'
    if 'DeskSelected' in headers and 'selected' not in role_fields:
        role_fields['selected'] = 'DeskSelected'
    return role_fields

# Setup structured logging
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            "level": record.levelname,
            "message": record.getMessage(),
            "module": record.module,
        }
        if hasattr(record, "zid"):
            log_data["zid"] = record.zid
        return json.dumps(log_data)

logger = logging.getLogger("kardenwort_desk")

def setup_logging(verbose=False, debug=False):
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())
    logger.addHandler(handler)
    if debug:
        logger.setLevel(logging.DEBUG)
    elif verbose:
        logger.setLevel(logging.INFO)
    else:
        logger.setLevel(logging.WARNING)

def print_structured_error(error_code, message, details=None):
    error_payload = {
        "error_code": error_code,
        "message": message,
    }
    if details:
        error_payload["details"] = details
    sys.stderr.write(json.dumps(error_payload) + "\n")

def get_deepl_key(config, base_dir):
    deepl_settings_file_val = config.get('environment', 'deepl_settings_file', fallback=None)
    if not deepl_settings_file_val:
        return None
        
    settings_path = (base_dir / deepl_settings_file_val).resolve()
    if not settings_path.exists():
        logger.warning(f"DeepL settings file not found: {settings_path}")
        return None
        
    settings = configparser.ConfigParser()
    read_success = False
    for enc in ('utf-8', 'utf-8-sig', 'utf-16', 'utf-16le', 'cp1252'):
        try:
            settings.read(settings_path, encoding=enc)
            read_success = True
            break
        except Exception:
            continue
    if not read_success:
        logger.error(f"Failed to decode DeepL settings file {settings_path}")
        return None
    
    salt = settings.get('Security', 'Salt', fallback='')
    secrets_path_val = settings.get('Security', 'SecretsPath', fallback='')
    if not secrets_path_val:
        return None
        
    secrets_path = (settings_path.parent / secrets_path_val).resolve()
    if not secrets_path.exists():
        logger.warning(f"DeepL secrets file not found: {secrets_path}")
        return None
        
    secrets = configparser.ConfigParser()
    read_success = False
    for enc in ('utf-8', 'utf-8-sig', 'utf-16', 'utf-16le', 'cp1252'):
        try:
            secrets.read(secrets_path, encoding=enc)
            read_success = True
            break
        except Exception:
            continue
    if not read_success:
        logger.error(f"Failed to decode DeepL secrets file {secrets_path}")
        return None
    
    obfuscated_key = secrets.get('DeepL', 'Key', fallback='')
    if not obfuscated_key:
        return None
        
    import base64
    try:
        decoded_bytes = base64.b64decode(obfuscated_key)
        if not salt:
            return obfuscated_key
            
        salt_bytes = salt.encode('utf-8')
        deobfuscated_bytes = bytearray()
        for i, b in enumerate(decoded_bytes):
            deobfuscated_bytes.append(b ^ salt_bytes[i % len(salt_bytes)])
            
        key_str = deobfuscated_bytes.decode('utf-8', errors='replace')
        if key_str.startswith('%%SEC%%'):
            return key_str[7:]
        else:
            return obfuscated_key
    except Exception as e:
        logger.warning(f"Error deobfuscating DeepL key: {e}. Using raw key.")
        return obfuscated_key

@contextlib.contextmanager
def file_lock(file_path):
    lock_file_path = file_path.with_suffix('.lock')
    lock_file = open(lock_file_path, 'w')
    try:
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if sys.platform == 'win32':
                import msvcrt
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        lock_file.close()
        try:
            os.remove(lock_file_path)
        except Exception:
            pass

def extract_zid(path):
    name = path.name
    match = re.match(r'^(\d{14})', name)
    return match.group(1) if match else "00000000000000"

def generate_slug(text, max_words=4):
    cleaned = re.sub(r'\{[^}]*\}', '', text)
    cleaned = re.sub(r'[^\w\s]', '', cleaned.lower())
    
    # Strip ZID from the start if the user highlighted text that begins with one
    cleaned = re.sub(r'^\s*\d{14}\s+', '', cleaned)
    
    words = cleaned.split()[:max_words]
    slug = '-'.join(words)
    return slug if slug else "untitled"

def load_tsv_rows(tsv_path):
    import csv
    comments = []
    headers = []
    data_rows = []
    
    lines_to_parse = []
    with open(tsv_path, 'r', encoding='utf-8') as f:
        for line in f:
            if not headers and not lines_to_parse and line.startswith('#'):
                comments.append(line.rstrip('\r\n'))
            else:
                lines_to_parse.append(line)
                
    reader = csv.reader(lines_to_parse, delimiter='\t')
    for i, row in enumerate(reader):
        if i == 0:
            headers = row
        else:
            data_rows.append(row)
            
    return comments, headers, data_rows

def save_tsv_rows_safely(tsv_path, comments, headers, data_rows):
    temp_path = tsv_path.with_suffix('.tsv.tmp')
    
    try:
        with open(temp_path, 'w', encoding='utf-8', newline='') as f:
            import csv
            writer = csv.writer(f, delimiter='\t', lineterminator='\n')
            for comment in comments:
                f.write(comment + '\n')
            writer.writerow(headers)
            for row in data_rows:
                writer.writerow(row)
                
        os.replace(temp_path, tsv_path)
    except Exception as e:
        if temp_path.exists():
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise e

def is_tsv_llm_filled(headers, data_rows, mapping):
    ai_cols = ['WordSourceMorphologyAI', 'WordSourceIPA']
    present_ai_cols = [col for col in ai_cols if col in headers]
    if present_ai_cols:
        for col in present_ai_cols:
            col_idx = headers.index(col)
            if any(len(row) > col_idx and row[col_idx].strip() for row in data_rows):
                return True
        return False
        
    fallback_cols = ['WordRussian', 'WordEnglish']
    for col in fallback_cols:
        if col in headers:
            col_idx = headers.index(col)
            if any(len(row) > col_idx and row[col_idx].strip() for row in data_rows):
                return True
    return False

def find_working_tsv(results_dir, zid, language):
    files = list(results_dir.glob(f"{zid}-*.{language}.tsv"))
    if not files:
        files = list(results_dir.glob(f"{zid}-*.tsv"))
    if files:
        return files[0]
    return None

def run_google_translation(text, source, target, config, resolved_paths):
    python_exe = resolved_paths['deep_translator_python']
    script_path = resolved_paths['translate_google_script']
    
    cmd = [
        str(python_exe),
        str(script_path),
        "--text", text,
        "--source", source,
        "--target", target,
    ]
    if config.getboolean('pipeline', 'use_local_fork', fallback=True):
        cmd.append("--use-local-fork")
        
    timeout = config.getint('timeouts', 'translation_timeout', fallback=60)
    logger.info(f"Running Google translation command: {' '.join(cmd)}")
    
    res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
    if res.returncode == 0:
        return res.stdout.strip()
    else:
        raise Exception(f"Google translation failed: {res.stderr}")

def run_deepl_translation(text, source, target, config, resolved_paths):
    python_exe = resolved_paths['deep_translator_python']
    script_path = resolved_paths['translate_deepl_script']
    
    deepl_key = get_deepl_key(config, resolved_paths['base_dir'])
    if not deepl_key:
        raise Exception("DeepL API key not configured or failed to resolve")
        
    cmd = [
        str(python_exe),
        str(script_path),
        "--text", text,
        "--source", source,
        "--target", target,
        "--deepl-api-key", deepl_key,
    ]
    if config.getboolean('pipeline', 'use_local_fork', fallback=True):
        cmd.append("--use-local-fork")
        
    timeout = config.getint('timeouts', 'translation_timeout', fallback=60)
    
    logged_cmd = cmd[:]
    if "--deepl-api-key" in logged_cmd:
        idx = logged_cmd.index("--deepl-api-key")
        if idx + 1 < len(logged_cmd):
            logged_cmd[idx + 1] = "********"
    logger.info(f"Running DeepL translation command: {' '.join(logged_cmd)}")
    
    res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
    if res.returncode == 0:
        return res.stdout.strip()
    else:
        raise Exception(f"DeepL translation failed: {res.stderr}")

def run_argos_translation(text, source, target, config, resolved_paths):
    python_exe = resolved_paths.get('argotranslate_python')
    script_path = resolved_paths.get('argotranslate_script')
    
    if not python_exe or not script_path:
        raise Exception("argotranslate_python or argotranslate_script not configured in config.ini")
        
    cmd = [
        str(python_exe),
        str(script_path),
        "-f", source,
        "-t", target
    ]
        
    # Double the timeout for local offline translation to handle model loading overhead and concurrent requests
    timeout = config.getint('timeouts', 'translation_timeout', fallback=60) * 2
    logger.info(f"Running Argos translation command: {' '.join(cmd)}")
    
    try:
        # Pass text via stdin to avoid command-line length limits and escaping issues on Windows
        res = subprocess.run(cmd, input=text, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
        if res.returncode == 0:
            return res.stdout.strip()
        else:
            raise Exception(f"Argos translation failed (code {res.returncode}): {res.stderr}")
    except subprocess.TimeoutExpired as e:
        raise Exception(f"Argos translation timed out after {timeout} seconds. Model loading under concurrent load may exceed limits: {e}")

def is_network_online_multi(hosts, port=53, timeout=1.0):
    if not hosts:
        return True
        
    def check_host(host):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(timeout)
            s.connect((host.strip(), port))
            s.close()
            return True
        except Exception:
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(hosts)) as executor:
        futures = [executor.submit(check_host, h) for h in hosts]
        for future in concurrent.futures.as_completed(futures):
            if future.result():
                return True
        return False

def translate_text(text, source, target, config, resolved_paths, provider):
    auto_fallback = config.getboolean('pipeline', 'auto_offline_fallback', fallback=True)
    
    check_ips_str = config.get('pipeline', 'fast_connectivity_check_ips', fallback=config.get('pipeline', 'fast_connectivity_check_ip', fallback='8.8.8.8, 1.1.1.1'))
    check_ips = [ip.strip() for ip in check_ips_str.split(',') if ip.strip()]
    
    if auto_fallback and check_ips and provider != 'argos':
        if not is_network_online_multi(hosts=check_ips):
            logger.warning(f"Fast connectivity check to {check_ips} failed. Bypassing online providers and going straight to Argos.")
            try:
                return run_argos_translation(text, source, target, config, resolved_paths)
            except Exception as ex2:
                logger.error(f"Argos offline fallback failed: {ex2}")
                raise ex2

    try:
        if provider == 'google':
            return run_google_translation(text, source, target, config, resolved_paths)
        elif provider == 'deepl':
            return run_deepl_translation(text, source, target, config, resolved_paths)
        elif provider == 'argos':
            return run_argos_translation(text, source, target, config, resolved_paths)
        elif provider in ('combined', 'intellifiller'):
            try:
                return run_google_translation(text, source, target, config, resolved_paths)
            except Exception as e:
                logger.warning(f"Google translation failed: {e}. Trying DeepL failover...")
                return run_deepl_translation(text, source, target, config, resolved_paths)
        else:
            raise Exception(f"Unsupported translation provider: {provider}")
    except Exception as e:
        if auto_fallback and provider != 'argos':
            # Verify if it's an actual offline event vs an API rate limit (429)
            if check_ips and not is_network_online_multi(hosts=check_ips):
                logger.warning(f"Primary provider '{provider}' failed: {e}. Network appears offline. Auto-fallback to Argos...")
                try:
                    return run_argos_translation(text, source, target, config, resolved_paths)
                except Exception as ex2:
                    logger.error(f"Argos offline fallback failed: {ex2}")
                    raise ex2
            else:
                # Network is online. Likely a rate limit or transient API error. Raise to trigger retry loop.
                logger.warning(f"Primary provider '{provider}' failed: {e}. Network is online. Raising exception for retries...")
                raise e
        else:
            if provider != 'argos':
                logger.warning(f"Provider '{provider}' failed: {e}. Auto-offline fallback is disabled.")
            raise e

def translate_lemmas_fast_path(lemmas, source, target, config, resolved_paths, provider):
    if not lemmas:
        return {}
        
    compact_line = "; ".join(lemmas)
    try:
        translated_line = translate_text(compact_line, source, target, config, resolved_paths, provider)
        parts = [p.strip() for p in translated_line.split(';')]
        parts = [p for p in parts if p]
        if len(parts) == len(lemmas):
            logger.info("Fast-path lemma translation aligned successfully.")
            return {lemmas[i]: parts[i] for i in range(len(lemmas))}
        else:
            logger.warning(f"Fast-path alignment failure: expected {len(lemmas)} parts, got {len(parts)}. Falling back to individual calls.")
    except Exception as e:
        logger.warning(f"Fast-path translation failed: {e}. Falling back to individual calls.")
        
    translations = {}
    for lemma in lemmas:
        try:
            translations[lemma] = translate_text(lemma, source, target, config, resolved_paths, provider)
        except Exception as e:
            logger.warning(f"Failed to translate lemma '{lemma}': {e}")
            translations[lemma] = ""
    return translations

EXIT_PARTIAL_TRANSLATION_PERSISTED = 2

class TranslationAlignmentError(Exception):
    def __init__(self, message, partial_dict=None):
        super().__init__(message)
        self.partial_dict = partial_dict or {}

_LEADING_PUNCT_RE = re.compile(r'^([.,!?;:\s]+)\s*')
_PUNCT_ONLY_RE = re.compile(r'^[.,!?;:\s]+$')

def clean_sentence_splits(lines):
    cleaned = list(lines)
    for i in range(1, len(cleaned)):
        line = cleaned[i]
        combined = line.strip()
        if not combined:
            continue
        if _PUNCT_ONLY_RE.match(combined):
            cleaned[i - 1] = cleaned[i - 1] + combined
            cleaned[i] = ""
        else:
            m = _LEADING_PUNCT_RE.match(combined)
            if m:
                punct = m.group(1).rstrip()
                remainder = combined[m.end():].strip()
                cleaned[i - 1] = cleaned[i - 1] + punct
                cleaned[i] = remainder
    return cleaned

def _split_long_line(line, max_chars=90):
    words = line.split()
    if not words:
        return []
    out = []
    cur = words[0]
    for word in words[1:]:
        candidate = f"{cur} {word}"
        if len(candidate) <= max_chars:
            cur = candidate
        else:
            out.append(cur)
            cur = word
    out.append(cur)
    return out

def split_single_mode_text(text, max_chars=90, abbrevs=None, terminators=".!?:"):
    import re
    if abbrevs is None:
        abbrevs = {
            "ca", "z.b", "usw", "uzw", "bzw", "etc", "t.con", "d.h", "u.a", "vgl", "ggf",
            "bspw", "u.u", "i.d.r", "bzgl", "evtl", "sog", "bsp", "z.zt", "m.e",
            "e.g", "i.e", "approx", "vs", "cf", "ltd", "co", "inc", "prof", "dr",
            "mr", "mrs", "ms"
        }
    
    escaped_terms = "".join(re.escape(c) for c in terminators)
    candidates = list(re.finditer(f'(?<=[{escaped_terms}])\\s+', text))
    
    splits = []
    last_idx = 0
    for m in candidates:
        split_pos = m.start()
        punc = text[split_pos - 1]
        
        if punc == '.':
            preceding_part = text[last_idx:split_pos]
            match = re.search(r'([a-zA-Z0-9.-]+)$', preceding_part.strip())
            if match:
                word = match.group(1).lower().rstrip('.')
                full_word = word
                if len(word) == 1 and preceding_part.strip().endswith(f" {word}"):
                    prev_part = preceding_part.strip()[:-len(word)].strip()
                    prev_match = re.search(r'([a-zA-Z0-9.-]+)$', prev_part)
                    if prev_match:
                        prev_word = prev_match.group(1).lower()
                        if prev_word.endswith('.'):
                            full_word = f"{prev_word} {word}"
                
                clean_word = full_word.replace(' ', '')
                if clean_word in abbrevs or clean_word.replace('.', '') in abbrevs:
                    continue
                clean_word_no_dot = clean_word.replace('.', '')
                if re.match(r'^[a-zA-Z]$', clean_word_no_dot):
                    continue
                if clean_word_no_dot.isdigit():
                    continue
        
        splits.append(split_pos)
        
    sentences = []
    start = 0
    for pos in splits:
        sentences.append(text[start:pos].strip())
        spaces_match = re.match(r'^\s+', text[pos:])
        start = pos + (spaces_match.end() if spaces_match else 0)
    sentences.append(text[start:].strip())
    sentences = [s for s in sentences if s]
    
    if not sentences:
        return []
    if len(sentences) <= 1 and len(text) > max_chars:
        return _split_long_line(text, max_chars)
    return sentences

def pad_sentences(sentences, original_text, words_before=0, words_after=0, max_words=0):
    if not (words_before or words_after):
        return sentences
        
    import re
    spans = []
    current_pos = 0
    for s in sentences:
        start = original_text.find(s, current_pos)
        if start == -1:
            start = current_pos
        end = start + len(s)
        spans.append((start, end))
        current_pos = end
        
    # Get all tokens using the repository's own tokenizer utility
    tokens = tok.build_word_list_internal(original_text, keep_spaces=True)
    
    # Pre-calculate the character span of each token in original_text
    token_spans = []
    curr_idx = 0
    for t in tokens:
        t_len = len(t["text"])
        token_spans.append({
            "start": curr_idx,
            "end": curr_idx + t_len,
            "is_word": t["is_word"]
        })
        curr_idx += t_len
        
    padded = []
    for i, (s_i, e_i) in enumerate(spans):
        pad_s = s_i
        pad_e = e_i
        
        # Word-based padding using the tokenizer spans
        if words_before > 0:
            words_before_tokens = [ts for ts in token_spans if ts["end"] <= s_i and ts["is_word"]]
            if len(words_before_tokens) >= words_before:
                pad_s = words_before_tokens[-words_before]["start"]
            else:
                pad_s = 0
                
        if words_after > 0:
            words_after_tokens = [ts for ts in token_spans if ts["start"] >= e_i and ts["is_word"]]
            if len(words_after_tokens) >= words_after:
                pad_e = words_after_tokens[words_after - 1]["end"]
            else:
                pad_e = len(original_text)
                
        padded_sentence = original_text[pad_s:pad_e].replace('\n', ' ').replace('\r', ' ').strip()
        padded_sentence = re.sub(r'\s+', ' ', padded_sentence)
        
        # Truncate context if it exceeds max_words
        if max_words > 0:
            padded_words = tok.build_word_list(padded_sentence)
            if len(padded_words) > max_words:
                target_sentence = sentences[i]
                target_words = tok.build_word_list(target_sentence)
                if target_words:
                    n_p = len(padded_words)
                    n_t = len(target_words)
                    
                    target_start_idx = -1
                    for j in range(n_p - n_t + 1):
                        if padded_words[j:j+n_t] == target_words:
                            target_start_idx = j
                            break
                            
                    if target_start_idx == -1:
                        target_start_idx = (n_p - n_t) // 2
                        
                    target_end_idx = target_start_idx + n_t - 1
                    
                    span = n_t
                    if span >= max_words:
                        crop_start = target_start_idx
                        crop_end = target_end_idx
                    else:
                        left_cap = (max_words - span) // 2
                        right_cap = max_words - span - left_cap
                        
                        actual_left_cap = min(left_cap, target_start_idx)
                        actual_right_cap = min(right_cap, n_p - 1 - target_end_idx)
                        
                        leftover_right = left_cap - actual_left_cap
                        leftover_left = right_cap - actual_right_cap
                        
                        if leftover_right > 0:
                            actual_right_cap = min(actual_right_cap + leftover_right, n_p - 1 - target_end_idx)
                        if leftover_left > 0:
                            actual_left_cap = min(actual_left_cap + leftover_left, target_start_idx)
                            
                        crop_start = target_start_idx - actual_left_cap
                        crop_end = target_end_idx + actual_right_cap
                        
                    p_tokens = tok.build_word_list_internal(padded_sentence, keep_spaces=True)
                    p_token_spans = []
                    p_curr_idx = 0
                    for t in p_tokens:
                        t_len = len(t["text"])
                        p_token_spans.append({
                            "start": p_curr_idx,
                            "end": p_curr_idx + t_len,
                            "is_word": t["is_word"]
                        })
                        p_curr_idx += t_len
                        
                    word_token_spans = [ts for ts in p_token_spans if ts["is_word"]]
                    if word_token_spans:
                        f_char = 0 if crop_start == 0 else word_token_spans[crop_start]["start"]
                        l_char = len(padded_sentence) if crop_end == len(word_token_spans) - 1 else word_token_spans[crop_end]["end"]
                        padded_sentence = padded_sentence[f_char:l_char].strip()
                        
        padded.append(padded_sentence)
        
    return padded



def _effective_text_mode(text, configured_text_mode=None):
    stripped = text.strip()
    return 'multi' if ('\n' in stripped or '\r' in stripped) else 'single'

def _validate_translated_line(orig_line, trans_line, idx, config):
    if not trans_line.strip():
        raise ValueError(f"Empty line returned for non-empty source at line index {idx}")
        
    word_count_check = config.getboolean('translation', 'translation_word_count_check', fallback=False)
    if word_count_check:
        orig_words = len(orig_line.split())
        trans_words = len(trans_line.split())
        if orig_words > 0:
            abs_tolerance = config.getint('translation', 'translation_word_count_abs_tolerance', fallback=5)
            if abs(orig_words - trans_words) > abs_tolerance:
                min_ratio = config.getfloat('translation', 'translation_word_count_min_ratio', fallback=0.25)
                max_ratio = config.getfloat('translation', 'translation_word_count_max_ratio', fallback=3.5)
                ratio = trans_words / orig_words
                if ratio < min_ratio or ratio > max_ratio:
                    raise ValueError(
                        f"Word count mismatch at line {idx}: original has {orig_words} words, "
                        f"translated has {trans_words} words (ratio {ratio:.2f} outside [{min_ratio}, {max_ratio}])"
                    )

def _build_chunks(lines, chunk_size, config):
    chunks = []
    adaptive_max_lines = config.getint('translation', 'translation_adaptive_max_lines', fallback=30)
    adaptive_max_chars = config.getint('translation', 'translation_adaptive_max_chars', fallback=1000)
    
    if chunk_size > 0:
        chunk = []
        chunk_indices = []
        for idx, line in enumerate(lines):
            if not line.strip():
                continue
            chunk.append(line)
            chunk_indices.append(idx)
            if len(chunk) == chunk_size:
                chunks.append((chunk, chunk_indices))
                chunk = []
                chunk_indices = []
        if chunk:
            chunks.append((chunk, chunk_indices))
    else:
        chunk = []
        chunk_indices = []
        chunk_char_count = 0
        for idx, line in enumerate(lines):
            if not line.strip():
                continue
            
            line_len = len(line)
            if len(chunk) >= adaptive_max_lines or (chunk_char_count + line_len) > adaptive_max_chars:
                chunks.append((chunk, chunk_indices))
                chunk = []
                chunk_indices = []
                chunk_char_count = 0
            
            chunk.append(line)
            chunk_indices.append(idx)
            chunk_char_count += line_len
        if chunk:
            chunks.append((chunk, chunk_indices))
    return chunks

def split_by_proportion(text, lengths):
    if not text or not lengths:
        return [text.strip()] if text else []
    if len(lengths) == 1:
        return [text.strip()]
    total = sum(lengths)
    if total == 0:
        n = len(lengths)
        equal = len(text) // n
        return [text[i * equal:(i + 1) * equal].strip() for i in range(n - 1)] + [text[(n - 1) * equal:].strip()]
    parts = []
    remaining = text.strip()
    remaining_total = total
    for i, length in enumerate(lengths):
        if i == len(lengths) - 1:
            parts.append(remaining.strip())
            break
        if not remaining:
            parts.extend([''] * (len(lengths) - i))
            break
        target_idx = int(round(len(remaining) * length / remaining_total))
        target_idx = max(1, min(target_idx, len(remaining) - 1))
        search_window = max(target_idx, len(remaining) - target_idx)
        split_idx = None
        for offset in range(search_window + 1):
            for candidate in (target_idx - offset, target_idx + offset):
                if 1 <= candidate < len(remaining) - 1 and remaining[candidate] == ' ':
                    split_idx = candidate
                    break
            if split_idx is not None:
                break
        if split_idx is None:
            split_idx = target_idx
        parts.append(remaining[:split_idx].strip())
        remaining = remaining[split_idx:].strip()
        remaining_total -= length
    return parts

def make_merge_split_marker(index):
    return f"[[KWSPLIT{index:04d}]]"

def split_merged_text_by_markers(text, markers):
    if not markers:
        return [text.strip()]
    parts = []
    remaining = text
    for marker in markers:
        marker_idx = remaining.find(marker)
        if marker_idx < 0:
            raise ValueError(f"Missing merge split marker in translated text: {marker}")
        parts.append(remaining[:marker_idx].strip())
        remaining = remaining[marker_idx + len(marker):]
    parts.append(remaining.strip())
    return parts

def _validate_translation_config(config):
    if not config.has_section('translation'):
        return
    split_mode = config.get('translation', 'translation_split_mode', fallback='newline_join')
    word_count_check = config.getboolean('translation', 'translation_word_count_check', fallback=False)
    if split_mode == 'proportional' and word_count_check:
        logger.warning(
            "Config validation warning: translation_word_count_check = true is incompatible with "
            "translation_split_mode = proportional. Forcing translation_word_count_check to false."
        )
        config.set('translation', 'translation_word_count_check', 'false')

def _write_translation_txt(text, effective_text_mode, sentence_translations_raw, out_path, *, save_flag, overwrite=False):
    if not save_flag:
        return
    if not sentence_translations_raw:
        return
    if not overwrite and out_path.exists() and out_path.stat().st_size > 0:
        return
        
    out_path.parent.mkdir(parents=True, exist_ok=True)
        
    if effective_text_mode == 'single':
        if 'FULL_TEXT' in sentence_translations_raw:
            translation_text_out = sentence_translations_raw['FULL_TEXT']
        else:
            translation_text_out = " ".join(sentence_translations_raw.get(i, "").strip() for i in sorted(sentence_translations_raw.keys()) if isinstance(i, int) and sentence_translations_raw.get(i, ""))
    else:
        num_lines = len(text.splitlines())
        translation_lines = [sentence_translations_raw.get(i, "").strip() for i in range(num_lines)]
        translation_text_out = "\n".join(translation_lines)
        
    out_path.write_text(translation_text_out, encoding='utf-8')

def resolve_translations(text, text_mode, data_rows, col_index, col_sentence_dest,
                         sentence_translations_raw, tsv_path, comments, headers,
                         *, persist=True, return_single=False):
    eff_mode = _effective_text_mode(text, text_mode)
    
    content_to_absolute = {}
    if eff_mode != 'single':
        c_idx = 0
        for a_idx, ln in enumerate(text.splitlines()):
            if ln.strip():
                content_to_absolute[c_idx] = a_idx
                c_idx += 1
    
    for row in data_rows:
        content_line_idx = 0
        if col_index != -1 and len(row) > col_index:
            try:
                content_line_idx = int(row[col_index]) - 1
            except ValueError:
                pass
        
        abs_idx = content_line_idx if eff_mode == 'single' else content_to_absolute.get(content_line_idx, 0)
        
        if col_sentence_dest != -1:
            while len(row) <= col_sentence_dest:
                row.append("")
            row[col_sentence_dest] = sentence_translations_raw.get(abs_idx, "")
            
    if persist and tsv_path:
        with file_lock(tsv_path):
            save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
            
    if return_single:
        if text_mode == 'single':
            if 'FULL_TEXT' in sentence_translations_raw:
                return sentence_translations_raw['FULL_TEXT']
            return " ".join([sentence_translations_raw.get(i, "").strip() for i in sorted(sentence_translations_raw.keys()) if isinstance(i, int) and sentence_translations_raw.get(i, "")])
        return sentence_translations_raw.get(0, "")
    return None

def translate_source_text(text, source_lang, target_lang, text_mode, config, resolved_paths, provider, chunk_callback=None):
    import time
    
    eff_mode = _effective_text_mode(text, text_mode)
    
    split_mode = config.get('translation', 'translation_split_mode', fallback='newline_join')
    chunk_size = config.getint('translation', 'translation_chunk_size', fallback=0)
    max_retries = config.getint('translation', 'translation_max_retries', fallback=3)
    retry_backoff = config.getfloat('translation', 'translation_retry_backoff', fallback=1.0)
    fix_sentence_splits = config.getboolean('translation', 'translation_fix_sentence_splits', fallback=False)
    wrap_max_chars = config.getint('translation', 'translation_wrap_max_chars', fallback=90)
    
    if eff_mode == 'single':
        if len(text) <= wrap_max_chars and '\n' not in text.strip():
            try:
                return {0: translate_text(text, source_lang, target_lang, config, resolved_paths, provider).strip()}
            except Exception as e:
                logger.error(f"Failed to translate main text: {e}")
                return {0: f"[Translation Error: {e}]"}
        else:
            abbrev_str = config.get('settings', 'anki_abbrev_list', fallback="")
            abbrev_set = {a.lower().rstrip('.') for a in abbrev_str.split()} if abbrev_str.strip() else None
            terminators = config.get('settings', 'anki_sentence_terminators', fallback=".!?:")
            if not terminators.strip():
                terminators = ".!?:"
            pseudo_lines = split_single_mode_text(text, wrap_max_chars, abbrevs=abbrev_set, terminators=terminators)
            words_before = config.getint('settings', 'anki_context_words_before', fallback=0)
            words_after = config.getint('settings', 'anki_context_words_after', fallback=0)
            max_words = config.getint('settings', 'anki_context_max_words', fallback=0)
            context_mode = config.get('settings', 'anki_context_mode', fallback='single').lower()
            
            apply_padding = False
            if words_before > 0 or words_after > 0:
                if context_mode == 'both' or context_mode == eff_mode:
                    apply_padding = True
                    
            try:
                if apply_padding:
                    padded_lines = pad_sentences(pseudo_lines, text, words_before, words_after, max_words=max_words)
                    
                    # 1. Translate the padded sentences for the TSV (SentenceDestination)
                    pseudo_translations = translate_source_text(
                        "\n".join(padded_lines), source_lang, target_lang, 'multi',
                        config, resolved_paths, provider, chunk_callback=chunk_callback
                    )
                    
                    # 2. Translate the unpadded pseudo-lines for the Translate View (.ru.txt) and TextDestination
                    # This preserves the literal piecemeal formatting that the user prefers (avoiding DeepL reformatting a giant text block)
                    full_text_trans = ""
                    try:
                        api_delay = config.getfloat('translation', 'translation_api_delay', fallback=0.0)
                        if api_delay > 0:
                            import time
                            time.sleep(api_delay)
                            
                        # Disable fallback for unpadded run so we don't accidentally downgrade the UI 
                        # to Argo if the network drops between the two passes.
                        original_fallback = config.get('pipeline', 'auto_offline_fallback', fallback='true')
                        config.set('pipeline', 'auto_offline_fallback', 'false')
                        try:
                            unpadded_translations = translate_source_text(
                                "\n".join(pseudo_lines), source_lang, target_lang, 'multi',
                                config, resolved_paths, provider
                            )
                            full_text_trans = " ".join(
                                unpadded_translations.get(i, "").strip() 
                                for i in sorted(unpadded_translations.keys()) 
                                if isinstance(i, int) and unpadded_translations.get(i, "")
                            )
                        finally:
                            config.set('pipeline', 'auto_offline_fallback', original_fallback)
                    except Exception as e:
                        logger.error(f"Failed to translate unpadded lines block: {e}")
                    
                    if full_text_trans:
                        pseudo_translations['FULL_TEXT'] = full_text_trans
                        
                    return pseudo_translations
                else:
                    return translate_source_text(
                        "\n".join(pseudo_lines), source_lang, target_lang, 'multi',
                        config, resolved_paths, provider, chunk_callback=chunk_callback
                    )
            except TranslationAlignmentError as tae:
                raise TranslationAlignmentError(
                    tae.args[0],
                    partial_dict=tae.partial_dict
                )
                
    raw_lines = text.splitlines()
    if fix_sentence_splits:
        lines = clean_sentence_splits(raw_lines)
    else:
        lines = raw_lines
        
    translations = {idx: "" for idx in range(len(lines))}
    
    if split_mode == 'line_by_line':
        first_failure = None
        for idx, line in enumerate(lines):
            if not line.strip():
                continue
            success = False
            last_err = None
            for attempt in range(1, max_retries + 1):
                try:
                    trans_line = translate_text(line, source_lang, target_lang, config, resolved_paths, provider)
                    _validate_translated_line(line, trans_line, idx, config)
                    translations[idx] = trans_line.strip()
                    success = True
                    break
                except Exception as e:
                    last_err = e
                    if attempt < max_retries:
                        time.sleep(retry_backoff)
            if not success:
                translations[idx] = ""
                if first_failure is None:
                    first_failure = (idx, last_err)
                    
            if chunk_callback:
                chunk_callback(translations)
                
        if first_failure is not None:
            failed_idx, failed_err = first_failure
            raise TranslationAlignmentError(
                f"Line-by-line translation failed at line {failed_idx}: {failed_err}",
                partial_dict=translations
            )
        return translations
        
    chunks = _build_chunks(lines, chunk_size, config)
    
    for chunk_text_list, indices in chunks:
        success = False
        last_err = None
        for attempt in range(1, max_retries + 1):
            try:
                if split_mode == 'newline_join':
                    joined_text = "\n".join(chunk_text_list)
                    translated_joined = translate_text(joined_text, source_lang, target_lang, config, resolved_paths, provider)
                    normalized = translated_joined.replace('\r\n', '\n').replace('\r', '\n')
                    translated_chunk_lines = normalized.split('\n')
                    
                    if len(translated_chunk_lines) > 1 and translated_chunk_lines[-1] == "":
                        translated_chunk_lines.pop()
                    if len(translated_chunk_lines) > 1 and translated_chunk_lines[0] == "":
                        translated_chunk_lines.pop(0)
                        
                elif split_mode == 'marker':
                    escaped_chunk_text_list = [line.replace("[[KWSPLIT", "__KWSPLITESC__") for line in chunk_text_list]
                    parts = []
                    markers = []
                    for i, line in enumerate(escaped_chunk_text_list):
                        if i > 0:
                            marker = make_merge_split_marker(i)
                            markers.append(marker)
                            parts.append(marker)
                        parts.append(line)
                    joined_text = " ".join(parts)
                    
                    translated_joined = translate_text(joined_text, source_lang, target_lang, config, resolved_paths, provider)
                    parts_split = split_merged_text_by_markers(translated_joined, markers)
                    translated_chunk_lines = [part.replace("__KWSPLITESC__", "[[KWSPLIT") for part in parts_split]
                    
                elif split_mode == 'proportional':
                    joined_text = " ".join(chunk_text_list)
                    translated_joined = translate_text(joined_text, source_lang, target_lang, config, resolved_paths, provider)
                    lengths = [len(line) for line in chunk_text_list]
                    translated_chunk_lines = split_by_proportion(translated_joined, lengths)
                else:
                    raise ValueError(f"Unknown translation_split_mode: {split_mode}")
                    
                if len(translated_chunk_lines) != len(chunk_text_list):
                    raise ValueError(f"Line count mismatch (expected {len(chunk_text_list)}, got {len(translated_chunk_lines)})")
                    
                for i, orig_line in enumerate(chunk_text_list):
                    _validate_translated_line(orig_line, translated_chunk_lines[i], indices[i], config)
                    
                for list_idx, target_idx in enumerate(indices):
                    translations[target_idx] = translated_chunk_lines[list_idx].strip()
                success = True
                break
            except Exception as e:
                last_err = e
                if attempt < max_retries:
                    time.sleep(retry_backoff)
                    
        if not success:
            first_rescue_failure = None
            for list_idx, target_idx in enumerate(indices):
                original_line = chunk_text_list[list_idx]
                try:
                    rescued_line = translate_text(original_line, source_lang, target_lang, config, resolved_paths, provider)
                    _validate_translated_line(original_line, rescued_line, target_idx, config)
                    translations[target_idx] = rescued_line.strip()
                except Exception as rescue_err:
                    translations[target_idx] = ""
                    if first_rescue_failure is None:
                        first_rescue_failure = (target_idx, rescue_err)
            if first_rescue_failure is not None:
                failed_idx, failed_err = first_rescue_failure
                raise TranslationAlignmentError(
                    f"Rescue translation failed for line {failed_idx}: {failed_err}",
                    partial_dict=translations
                )
                
        if chunk_callback:
            chunk_callback(translations)
            
    return translations

def run_headless_intellifiller(tsv_path, prompt_name, config, resolved_paths, selected_rows=None):
    python_exe = resolved_paths['kardenwort_python']
    headless_script = resolved_paths['intellifiller_headless']
    
    cmd = [
        str(python_exe),
        str(headless_script),
        "--tsv", str(tsv_path),
        "--prompt", prompt_name,
    ]
    
    if selected_rows:
        rows_str = ",".join(str(r) for r in selected_rows)
        cmd.extend(["--selected-rows", rows_str])
    
    timeout = config.getint('timeouts', 'intellifiller_timeout', fallback=120)
    logger.info(f"Running headless IntelliFiller command: {' '.join(cmd)}")
    
    res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', timeout=timeout)
    if res.returncode == 0:
        logger.info("Headless IntelliFiller finished successfully.")
        return True
    else:
        logger.error(f"Headless IntelliFiller failed with exit code {res.returncode}: {res.stderr}")
        return False

def run_headless_intellifiller_async(tsv_path, prompt_name, config, resolved_paths, selected_rows=None):
    python_exe = sys.executable
    desk_script = Path(__file__).resolve()
    
    if selected_rows is None:
        try:
            _, _, data_rows = load_tsv_rows(tsv_path)
            selected_rows = list(range(len(data_rows)))
        except Exception:
            selected_rows = []
            
    if not selected_rows:
        return
        
    rows_str = ",".join(str(r) for r in selected_rows)
    
    cmd = [
        str(python_exe),
        str(desk_script),
        "batch-worker",
        "--tsv", str(tsv_path),
        "--prompt", prompt_name,
        "--rows", rows_str
    ]
        
    logger.info(f"Kicking off background batch-worker: {' '.join(cmd)}")
    if sys.platform == 'win32':
        creationflags = 0x08000000 | 0x00000200
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True
        )
    else:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True
        )

def run_progressive_worker_async(tsv_path, language, target_lang, prompt_name, lemmas_provider, word_translations_empty, skip_intellifiller=False, text_mode='single'):
    python_exe = sys.executable
    desk_script = Path(__file__).resolve()
    cmd = [
        str(python_exe),
        str(desk_script),
        "progressive-worker",
        "--tsv", str(tsv_path),
        "--language", language,
        "--target-lang", target_lang,
        "--prompt", prompt_name,
        "--provider", lemmas_provider,
        "--word-empty", str(word_translations_empty),
        "--text-mode", text_mode
    ]
    if skip_intellifiller:
        cmd.append("--skip-intellifiller")
    logger.info(f"Kicking off background progressive-worker: {' '.join(cmd)}")
    if sys.platform == 'win32':
        creationflags = 0x08000000 | 0x00000200
        subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, creationflags=creationflags, close_fds=True
        )
    else:
        subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, close_fds=True
        )

def run_detached_import(favorites_tsv_path, config, resolved_paths, zid):
    python_exe = resolved_paths['kardenwort_python']
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    runner_script = kardenwort_workspace / "src" / "kardenwort" / "core" / "kardenwort_runner.py"
    
    cmd = [
        str(python_exe),
        str(runner_script),
        "--import-only",
        "--tsv", str(favorites_tsv_path),
        "--play-sound-on-completion"
    ]
    
    log_file_path = favorites_tsv_path.parent / f"{zid}-import.log"
    log_file = open(log_file_path, 'w', encoding='utf-8')
    
    logger.info(f"Launching detached import: {' '.join(cmd)}")
    
    show_window = config.getboolean('settings', 'show_import_window', fallback=False)
    if sys.platform == 'win32':
        # CREATE_NEW_PROCESS_GROUP = 0x00000200
        # DETACHED_PROCESS = 0x00000008
        # CREATE_NO_WINDOW = 0x08000000
        if show_window:
            creationflags = 0x00000200 | 0x00000008
        else:
            creationflags = 0x00000200 | 0x08000000
        p = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
            close_fds=True
        )
    else:
        p = subprocess.Popen(
            cmd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True
        )
        
    return p.pid, str(log_file_path)

def run_synchronous_import(favorites_tsv_path, config, resolved_paths):
    python_exe = resolved_paths['kardenwort_python']
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    runner_script = kardenwort_workspace / "src" / "kardenwort" / "core" / "kardenwort_runner.py"
    
    cmd = [
        str(python_exe),
        str(runner_script),
        "--import-only",
        "--tsv", str(favorites_tsv_path),
    ]
    
    logger.info(f"Running synchronous import: {' '.join(cmd)}")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', check=True)
        return True, res.stdout
    except subprocess.CalledProcessError as e:
        return False, e.stderr

def prepare_lookup_tsv(text, language, target_lang, config, resolved_paths, zid, *, ttl_seconds, cache_key, text_mode='single'):
    eff_mode = _effective_text_mode(text, text_mode)
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    kw_config = load_kardenwort_config(kardenwort_workspace)
    
    results_dir_name = kw_config.get('project_structure', 'generated_results_dir', fallback='results')
    results_dir = (kardenwort_workspace / results_dir_name).resolve()
    results_dir.mkdir(parents=True, exist_ok=True)
    
    working_tsv_path = results_dir / cache_key
    
    import time
    
    # Clean up stale update.js files (> 5 minutes old) to prevent clutter
    try:
        now = time.time()
        for f in results_dir.rglob("*.update.js"):
            if f.is_file() and (now - f.stat().st_mtime) > 300:
                try:
                    f.unlink()
                except OSError:
                    pass
    except Exception:
        pass
        
    import re
    
    if working_tsv_path.exists():
        if ttl_seconds <= 0 or (time.time() - working_tsv_path.stat().st_mtime) <= ttl_seconds:
            return working_tsv_path
            
    if ttl_seconds > 0:
        m = re.match(r'^\d{14}-(.+)', cache_key)
        if m:
            slug_part = m.group(1)
            for cached_file in results_dir.glob(f"*-{slug_part}"):
                if cached_file.is_file():
                    if (time.time() - cached_file.stat().st_mtime) <= ttl_seconds:
                        return cached_file
            
    # Clean up any leftover update.js from previous sessions to avoid polling stale data
    update_js_path = working_tsv_path.with_suffix('.update.js')
    if update_js_path.exists():
        try:
            os.remove(update_js_path)
        except OSError:
            pass
            
    stem = cache_key
    if stem.endswith('.tsv'):
        stem = stem[:-4]
    source_text_path = results_dir / f"{stem}.txt"
    
    wrap_max_chars = config.getint('translation', 'translation_wrap_max_chars', fallback=90)
    
    save_source_text = config.getboolean('settings', 'save_source_text', fallback=True)
    if save_source_text:
        if eff_mode == 'single':
            source_text_path.write_text(text, encoding='utf-8')
        elif not source_text_path.exists():
            source_text_path.write_text(text, encoding='utf-8')
        
    mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
    fields = list(mapping['fields'].keys())
    field_mapping = build_field_mapping(mapping, 'word')
    
    lemma_index_rel = config.get('languages', f'{language}_lemma_index')
    lemma_override_rel = config.get('languages', f'{language}_lemma_override')
    
    lemma_index_file = kardenwort_workspace / lemma_index_rel
    lemma_override_file = kardenwort_workspace / lemma_override_rel
    
    python_exe = resolved_paths['kardenwort_python']
    kardenwort_script = kardenwort_workspace / "src" / "kardenwort" / "core" / "kardenwort.py"
    
    text_file_to_pass = source_text_path
    temp_file_path = None
    
    try:
        abbrev_str = config.get('settings', 'anki_abbrev_list', fallback="")
        abbrev_set = {a.lower().rstrip('.') for a in abbrev_str.split()} if abbrev_str.strip() else None
        terminators = config.get('settings', 'anki_sentence_terminators', fallback=".!?:")
        if not terminators.strip():
            terminators = ".!?:"
        use_temp = (eff_mode == 'single') or (not save_source_text)
        if use_temp:
            if eff_mode == 'single':
                split_lines = split_single_mode_text(text, wrap_max_chars, abbrevs=abbrev_set, terminators=terminators)
                temp_content = "\n".join(split_lines)
            else:
                temp_content = text
            temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', encoding='utf-8', delete=False)
            temp_file_path = Path(temp_file.name)
            try:
                temp_file.write(temp_content)
            finally:
                temp_file.close()
            text_file_to_pass = temp_file_path
            
        cmd = [
            str(python_exe),
            str(kardenwort_script),
            "--type", "word",
            "--language", language,
            "--deduplication-scope", "global",
            "--lemma-index-file", str(lemma_index_file),
            "--lemma-override-file", str(lemma_override_file),
            "--sentence-context-size", "0",
            "--anki-csv-header", json.dumps(fields),
            "--anki-field-mapping", json.dumps(field_mapping),
            "--output-file", str(working_tsv_path),
            "--text1-file", str(text_file_to_pass),
            "--tts-destination-lang", target_lang
        ]
        
        if language == "de":
            de_dictionary_file = kw_config.get('language_resources', 'dictionary_file_de', fallback='german.dic')
            de_dict_path = kardenwort_workspace / "data" / de_dictionary_file
            cmd.extend([
                "--de-fix-genitive",
                "--de-dictionary-file", str(de_dict_path),
            ])
            
        kardenwort_timeout = config.getint('timeouts', 'kardenwort_timeout', fallback=120)
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        
        logger.info(f"Running kardenwort.py: {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True, timeout=kardenwort_timeout, env=env, capture_output=True, text=True, encoding='utf-8')
        except subprocess.TimeoutExpired as e:
            print_structured_error("TIMEOUT", f"kardenwort.py timed out after {kardenwort_timeout} seconds")
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            print_structured_error("KARDENWORT_FAILED", f"kardenwort.py failed with exit code {e.returncode}", {"stderr": e.stderr})
            sys.exit(1)

        context_mode = config.get('settings', 'anki_context_mode', fallback='single').lower()
        words_before = config.getint('settings', 'anki_context_words_before', fallback=0)
        words_after = config.getint('settings', 'anki_context_words_after', fallback=0)
        max_words = config.getint('settings', 'anki_context_max_words', fallback=0)
        
        apply_padding = False
        if words_before > 0 or words_after > 0:
            if context_mode == 'both':
                apply_padding = True
            elif context_mode == eff_mode:
                apply_padding = True

        if apply_padding and working_tsv_path.exists():
            try:
                comments, headers, data_rows = load_tsv_rows(working_tsv_path)
                col_src_idx = headers.index('SentenceSourceIndex') if 'SentenceSourceIndex' in headers else -1
                col_src_sent = headers.index('SentenceSource') if 'SentenceSource' in headers else -1
                if col_src_idx != -1 and col_src_sent != -1:
                    if eff_mode == 'single':
                        sentences = split_single_mode_text(text, wrap_max_chars, abbrevs=abbrev_set, terminators=terminators)
                    else:
                        sentences = [ln.strip() for ln in text.splitlines()]
                    padded_sentences = pad_sentences(sentences, text, words_before, words_after, max_words=max_words)
                    modified = False
                    for row in data_rows:
                        if len(row) > col_src_idx and len(row) > col_src_sent:
                            try:
                                idx = int(row[col_src_idx]) - 1
                                if 0 <= idx < len(padded_sentences):
                                    row[col_src_sent] = padded_sentences[idx]
                                    modified = True
                            except ValueError:
                                pass
                    if modified:
                        save_tsv_rows_safely(working_tsv_path, comments, headers, data_rows)
            except Exception as e:
                logger.error(f"Failed to apply sentence context padding: {e}")
    finally:
        if temp_file_path is not None:
            try:
                os.remove(temp_file_path)
            except OSError:
                pass

    return working_tsv_path

SPLIT_GAP_LIMIT = 60

def resolve_anchored_positions(inflected_words, source_word_cleans, gap_limit):
    """
    Finds the set of non-overlapping minimum-span ordered tuples of source-word positions.
    inflected_words: list of lowered, cleaned inflected form words.
    source_word_cleans: list of lowered, cleaned source words.
    gap_limit: int, maximum allowed distance between consecutive positions.
    """
    if len(inflected_words) < 2:
        return set(), False

    # Collect occurrence lists
    occs = []
    for word in inflected_words:
        occs.append([idx for idx, w in enumerate(source_word_cleans) if w == word])

    # If any of the words are not in the source text, no tuple can be formed
    if any(not lst for lst in occs):
        return set(), False

    valid_tuples = []
    k = len(inflected_words)

    def backtrack(step, current_tuple):
        if step == k:
            valid_tuples.append(tuple(current_tuple))
            return
        
        prev_pos = current_tuple[-1] if current_tuple else None
        for pos in occs[step]:
            if prev_pos is not None:
                if pos <= prev_pos:
                    continue
                if pos - prev_pos > gap_limit:
                    continue
            current_tuple.append(pos)
            backtrack(step + 1, current_tuple)
            current_tuple.pop()

    backtrack(0, [])

    if not valid_tuples:
        return set(), False

    # Sort candidates by (span, start_pos)
    valid_tuples.sort(key=lambda t: (t[-1] - t[0], t[0]))

    used_positions = set()
    selected_positions = set()
    
    for t in valid_tuples:
        if any(p in used_positions for p in t):
            continue
        for p in t:
            used_positions.add(p)
            selected_positions.add(p)

    return selected_positions, len(selected_positions) > 0

def run_render_flow(text, language, zid, text_mode, config, resolved_paths, zoom_level="100", theme="dark", tsv_path=None, split_gap_limit=60):
    target_lang = config.get('settings', 'default_target_language', fallback='ru')
    
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    kw_config = load_kardenwort_config(kardenwort_workspace)
    results_dir_name = kw_config.get('project_structure', 'generated_results_dir', fallback='results')
    results_dir = (kardenwort_workspace / results_dir_name).resolve()
    
    slug = generate_slug(text)
    cache_key = f"{zid}-{slug}.{language}.tsv"
    
    eff_mode = _effective_text_mode(text, text_mode)
    
    if tsv_path and Path(tsv_path).exists():
        working_tsv_path = Path(tsv_path)
    else:
        working_tsv_path = prepare_lookup_tsv(
            text, language, target_lang, config, resolved_paths, zid,
            ttl_seconds=0, cache_key=cache_key, text_mode=eff_mode
        )
    
    mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
    comments, headers, data_rows = load_tsv_rows(working_tsv_path)

    llm_filled = is_tsv_llm_filled(headers, data_rows, mapping)
    
    main_text_provider = config.get('pipeline', 'text_base_provider', fallback=config.get('pipeline', 'lemma_base_provider', fallback='google'))
    lemmas_provider = config.get('pipeline', 'lemma_reprocess_provider', fallback='intellifiller')
    
    role_fields = get_role_fields(mapping, headers)
        
    col_highlighted = headers.index(role_fields['selected']) if 'selected' in role_fields and role_fields['selected'] in headers else -1
    col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
    col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else -1
    col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else -1
    col_inflected = headers.index(role_fields['inflected']) if 'inflected' in role_fields and role_fields['inflected'] in headers else -1
    
    is_progressive = config.get('rendering', 'display_mode', fallback='progressive') == 'progressive'
    auto_inject_updates = config.getboolean('rendering', 'auto_inject_updates', fallback=True)
    run_base = config.get('triggers', 'run_lemma_base_translation', fallback='auto')
    run_text = config.get('triggers', 'run_text_translation', fallback='auto')
    run_enrich = config.get('triggers', 'run_lemma_enrichment', fallback='auto')
    base_provider = config.get('pipeline', 'lemma_base_provider', fallback='google')
    enrich_provider = config.get('pipeline', 'lemma_reprocess_provider', fallback='intellifiller')
    
    source_text_path = working_tsv_path.with_suffix('.txt')
    if eff_mode == 'single':
        source_text_path.write_text(text, encoding='utf-8')
    elif not source_text_path.exists():
        source_text_path.write_text(text, encoding='utf-8')
            
    sentence_translated = False
    if col_sentence_dest != -1:
        if any(len(row) > col_sentence_dest and row[col_sentence_dest].strip() for row in data_rows):
            sentence_translated = True
            
    word_translations_empty = True
    if col_word_dest != -1:
        if any(len(row) > col_word_dest and row[col_word_dest].strip() for row in data_rows):
            word_translations_empty = False
            
    # If monolithic mode and run_base is auto, run base translation synchronously
    if not is_progressive and run_base == 'auto':
        col_index = headers.index('SentenceSourceIndex') if 'SentenceSourceIndex' in headers else -1
        try:
            if not sentence_translated:
                sentence_translations_raw = translate_source_text(text, language, target_lang, text_mode, config, resolved_paths, base_provider)
                resolve_translations(
                    text, text_mode, data_rows, col_index, col_sentence_dest,
                    sentence_translations_raw, working_tsv_path, comments, headers,
                    persist=True, return_single=False
                )
                eff_mode = _effective_text_mode(text, text_mode)
                translation_text_path = results_dir / f"{zid}-{slug}.{target_lang}.txt"
                save_translation_text = config.getboolean('settings', 'save_translation_text', fallback=False)
                _write_translation_txt(text, eff_mode, sentence_translations_raw, translation_text_path, save_flag=save_translation_text, overwrite=True)
                
 
        except TranslationAlignmentError as tae:
            logger.error(f"Monolithic translation alignment error: {tae}")
            sentence_translations_raw = tae.partial_dict
            resolve_translations(
                text, text_mode, data_rows, col_index, col_sentence_dest,
                sentence_translations_raw, working_tsv_path, comments, headers,
                persist=True, return_single=False
            )
            eff_mode = _effective_text_mode(text, text_mode)
            translation_text_path = results_dir / f"{zid}-{slug}.{target_lang}.txt"
            save_translation_text = config.getboolean('settings', 'save_translation_text', fallback=False)
            _write_translation_txt(text, eff_mode, sentence_translations_raw, translation_text_path, save_flag=save_translation_text, overwrite=True)
            run_enrich = 'manual'
                
        if word_translations_empty:
            lemmas_to_translate = list(set(row[col_lemma] for row in data_rows if len(row) > col_lemma and row[col_lemma].strip()))
            lemma_translations = translate_lemmas_fast_path(lemmas_to_translate, language, target_lang, config, resolved_paths, base_provider)
            
            for row in data_rows:
                if col_lemma != -1 and len(row) > col_lemma:
                    lemma_val = row[col_lemma]
                    if col_word_dest != -1:
                        while len(row) <= col_word_dest:
                            row.append("")
                        row[col_word_dest] = lemma_translations.get(lemma_val, "")
            with file_lock(working_tsv_path):
                save_tsv_rows_safely(working_tsv_path, comments, headers, data_rows)
                
    translation_text_path = results_dir / f"{zid}-{slug}.{target_lang}.txt"
    
    extracted_translations = {}
    col_index = headers.index('SentenceSourceIndex') if 'SentenceSourceIndex' in headers else -1
    for row in data_rows:
        content_line_idx = 0
        if col_index != -1 and len(row) > col_index:
            try:
                content_line_idx = int(row[col_index]) - 1
            except ValueError:
                pass
        if col_sentence_dest != -1 and len(row) > col_sentence_dest:
            extracted_translations[content_line_idx] = row[col_sentence_dest]
            
    sentence_translations = {}
    if not sentence_translated and 'sentence_translations_raw' in locals():
        sentence_translations = sentence_translations_raw
    elif translation_text_path.exists():
        translation_lines = translation_text_path.read_text(encoding='utf-8').splitlines()
        if eff_mode == 'single':
            sentence_translations[0] = " ".join(translation_lines)
        else:
            for a_idx, ln in enumerate(translation_lines):
                sentence_translations[a_idx] = ln
            # fill missing lines just in case
            for a_idx in range(len(text.splitlines())):
                if a_idx not in sentence_translations:
                    sentence_translations[a_idx] = ""
    else:
        if eff_mode == 'single':
            sentence_translations[0] = extracted_translations.get(0, "")
        else:
            c_idx = 0
            for a_idx, ln in enumerate(text.splitlines()):
                if ln.strip():
                    sentence_translations[a_idx] = extracted_translations.get(c_idx, "")
                    c_idx += 1
                else:
                    sentence_translations[a_idx] = ""
            
    save_translation_text = config.getboolean('settings', 'save_translation_text', fallback=False)
    translation_text_path = results_dir / f"{zid}-{slug}.{target_lang}.txt"
    eff_mode = _effective_text_mode(text, text_mode)
    _write_translation_txt(text, eff_mode, sentence_translations, translation_text_path, save_flag=save_translation_text, overwrite=False)
            
    worker_launched = False
    if not llm_filled:
        prompt_name = config.get('languages', f'{language}_prompt')
        
        if is_progressive:
            if (run_text == 'auto' and not sentence_translated) or (run_base == 'auto' and word_translations_empty) or (run_enrich == 'auto' and enrich_provider == 'intellifiller'):
                skip_intellifiller = (run_enrich == 'manual') or (enrich_provider == 'none')
                run_progressive_worker_async(working_tsv_path, language, target_lang, prompt_name, base_provider, word_translations_empty, skip_intellifiller, eff_mode)
                worker_launched = True
        else:
            # Monolithic mode enrichment
            if run_enrich == 'auto' and enrich_provider == 'intellifiller':
                run_headless_intellifiller(working_tsv_path, prompt_name, config, resolved_paths)
                comments, headers, data_rows = load_tsv_rows(working_tsv_path)
                    
    token_to_rows = {}
    row_candidates = {}
    for row_id, row in enumerate(data_rows):
        lemma_val = row[col_lemma] if col_lemma != -1 and len(row) > col_lemma else ""
        inflected_val = row[col_inflected] if col_inflected != -1 and len(row) > col_inflected else ""
        
        candidates = set()
        for val in (lemma_val, inflected_val):
            if val:
                clean_val = "".join(ch for ch in val.lower() if ch.isalnum() or ch == "'")
                if clean_val:
                    candidates.add(clean_val)
                parts = re.findall(r"[\w']+", val.lower())
                if len(parts) > 1:
                    for part in parts:
                        clean_part = "".join(ch for ch in part if ch.isalnum() or ch == "'")
                        if clean_part:
                            candidates.add(clean_part)
        row_candidates[row_id] = candidates
        for cand in candidates:
            if cand not in token_to_rows:
                token_to_rows[cand] = []
            token_to_rows[cand].append(row_id)
            
    source_tokens = tok.build_word_list_internal(text, keep_spaces=True)
    source_word_cleans = [t["lower_clean"] for t in source_tokens if t.get("is_word") and "lower_clean" in t]

    single_word_rows = set()
    anchored_positions = {}
    for row_id, row in enumerate(data_rows):
        inflected_val = row[col_inflected] if col_inflected != -1 and len(row) > col_inflected else ""
        inf_words = []
        if inflected_val:
            inf_words = [tok.utf8_to_lower("".join(ch for ch in p if ch.isalnum() or ch == "'"))
                         for p in re.findall(r"[\w']+", inflected_val)]
            inf_words = [w for w in inf_words if w]
        
        if len(inf_words) >= 2:
            pos_set, ok = resolve_anchored_positions(inf_words, source_word_cleans, split_gap_limit)
            if ok:
                anchored_positions[row_id] = pos_set
            else:
                anchored_positions[row_id] = set()
        else:
            single_word_rows.add(row_id)
            anchored_positions[row_id] = set()

    paired_rows = {row_id for row_id, pos_set in anchored_positions.items() if pos_set}
    
    col_index = headers.index('SentenceSourceIndex') if 'SentenceSourceIndex' in headers else -1
    row_to_c_idx = {}
    if col_index != -1:
        for row_id, row in enumerate(data_rows):
            if len(row) > col_index:
                try:
                    row_to_c_idx[row_id] = int(row[col_index]) - 1
                except ValueError:
                    row_to_c_idx[row_id] = -1
                    
    absolute_to_c_idx = {}
    if eff_mode != 'single':
        c_idx = 0
        for a_idx, ln in enumerate(text.splitlines()):
            if ln.strip():
                absolute_to_c_idx[a_idx] = c_idx
                c_idx += 1

    span_htmls = []
    word_counter = 0
    current_a_idx = 0
    for token in source_tokens:
        tok_text = token["text"]
        text_escaped = tok_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        if token["is_word"]:
            lower_clean = token.get("lower_clean", "")
            mapped_rows = token_to_rows.get(lower_clean, [])
            
            if eff_mode != 'single' and col_index != -1:
                curr_c_idx = absolute_to_c_idx.get(current_a_idx, -1)
                mapped_rows = [r_idx for r_idx in mapped_rows if row_to_c_idx.get(r_idx, -1) == curr_c_idx]
                
            token["filtered_mapped_rows"] = mapped_rows
            
            classes = ["word"]
            if mapped_rows:
                is_paired = any(word_counter in anchored_positions.get(r_idx, set()) for r_idx in mapped_rows)
                if is_paired:
                    classes.append("highlight-purple")
                elif any(r_idx in single_word_rows for r_idx in mapped_rows):
                    classes.append("highlight-orange")
                else:
                    classes.append("not-connected")
            else:
                classes.append("not-connected")
            classes_str = " ".join(classes)
            span_htmls.append(
                f'<span class="{classes_str}" data-word-idx="{token["visual_idx"]}" '
                f'data-line-idx="{current_a_idx}" '
                f'data-lower-clean="{lower_clean}">{text_escaped}</span>'
            )
            word_counter += 1
        else:
            if tok_text in ("\\N", "\\n"):
                span_htmls.append("<br>")
                current_a_idx += 1
            elif "\n" in tok_text or "\r" in tok_text:
                normalized = tok_text.replace("\r\n", "\n").replace("\r", "\n")
                parts = normalized.split("\n")
                current_a_idx += len(parts) - 1
                span_htmls.append("<br>".join(parts))
            else:
                span_htmls.append(text_escaped)
                
    source_html = "".join(span_htmls)
    
    sentence_htmls = []
    if is_progressive and run_text == 'auto' and not sentence_translated:
        sentence_html = '<div class="skeleton-loader" data-pending="true" style="width: 100%; max-width: 500px;"></div>'
    else:
        for idx in sorted(sentence_translations.keys()):
            trans = sentence_translations[idx]
            if trans:
                safe_trans = html.escape(trans)
                sentence_htmls.append(f"<div>{safe_trans}</div>")
            else:
                sentence_htmls.append("<div>&nbsp;</div>")
        sentence_html = "".join(sentence_htmls)
    
    col_morph = headers.index(role_fields['morphology']) if 'morphology' in role_fields else -1
    col_ipa = headers.index(role_fields['ipa']) if 'ipa' in role_fields else -1

    header_cols = ["Inflected", "Lemma", "Translation", "IPA", "Morphology"]
    table_header_html = "<tr>" + "".join(f"<th>{h}</th>" for h in header_cols) + "</tr>"

    table_rows = []
    for row_id, row in enumerate(data_rows):
        lemma_val = row[col_lemma] if col_lemma != -1 and len(row) > col_lemma else ""
        inflected_val = row[col_inflected] if col_inflected != -1 and len(row) > col_inflected else ""
        trans_val = row[col_word_dest] if col_word_dest != -1 and len(row) > col_word_dest else ""
        morph_val = row[col_morph] if col_morph != -1 and len(row) > col_morph else ""
        ipa_val = row[col_ipa] if col_ipa != -1 and len(row) > col_ipa else ""
        
        # Skeleton loaders for cells if they are pending in progressive mode:
        if is_progressive:
            if run_base == 'auto' and not trans_val.strip() and word_translations_empty:
                trans_val = '<span class="skeleton-loader" style="width: 60px;"></span>'
            if run_enrich == 'auto' and enrich_provider == 'intellifiller' and not ipa_val.strip() and not llm_filled:
                ipa_val = '<span class="skeleton-loader" style="width: 50px;"></span>'
            if run_enrich == 'auto' and enrich_provider == 'intellifiller' and not morph_val.strip() and not llm_filled:
                morph_val = '<span class="skeleton-loader" style="width: 80px;"></span>'
        
        lemma_class = "editable" if 'WordSource' in mapping.get('desk_editable', 'editable_columns', fallback='') else ""
        trans_class = "editable" if 'WordDestination' in mapping.get('desk_editable', 'editable_columns', fallback='') else ""
        inflected_class = "editable" if 'WordSourceInflectedForm' in mapping.get('desk_editable', 'editable_columns', fallback='') else ""
        
        row_highlight_class = "highlight-purple" if row_id in paired_rows else "highlight-orange"
        
        is_selected = "0"
        if col_highlighted != -1:
            highlighted_val = row[col_highlighted] if len(row) > col_highlighted else ""
            if highlighted_val.strip().lower() in ["1", "true"]:
                is_selected = "1"

        table_rows.append(
            f'<tr data-row-id="{row_id}" data-selected="{is_selected}" class="{row_highlight_class}">'
            f'<td class="{inflected_class}" data-col="WordSourceInflectedForm"><div class="scrollable-cell">{inflected_val}</div></td>'
            f'<td class="{lemma_class}" data-col="WordSource"><div class="scrollable-cell">{lemma_val}</div></td>'
            f'<td class="{trans_class}" data-col="WordDestination"><div class="scrollable-cell">{trans_val}</div></td>'
            f'<td><div class="scrollable-cell">{ipa_val}</div></td>'
            f'<td><div class="scrollable-cell">{morph_val}</div></td>'
            f'</tr>'
        )
    table_rows_html = "\n".join(table_rows)
    
    token_manifest = []
    word_counter = 0
    for token in source_tokens:
        tok_data = {
            "text": token["text"],
            "is_word": token["is_word"],
            "visual_idx": token["visual_idx"]
        }
        if token["is_word"] and "lower_clean" in token:
            tok_data["lower_clean"] = token["lower_clean"]
            mapped_rows = token.get("filtered_mapped_rows", token_to_rows.get(token["lower_clean"], []))
            filtered_rows = []
            for r_idx in mapped_rows:
                if r_idx in single_word_rows:
                    filtered_rows.append(r_idx)
                elif word_counter in anchored_positions.get(r_idx, set()):
                    filtered_rows.append(r_idx)
            if filtered_rows:
                tok_data["row_ids"] = filtered_rows
            word_counter += 1
        token_manifest.append(tok_data)
        
    html_page = """<!DOCTYPE html>
<!-- saved from url=(0014)about:internet -->
<html>
<head>
<meta charset="utf-8">
<meta http-equiv="X-UA-Compatible" content="IE=edge">
<meta http-equiv="Cache-Control" content="no-cache, no-store, must-revalidate">
<meta http-equiv="Pragma" content="no-cache">
<meta http-equiv="Expires" content="0">
<style>
  

  

  *, *:before, *:after {
    -webkit-box-sizing: border-box;
    -moz-box-sizing: border-box;
    box-sizing: border-box;
  }
  /* For standard Webkit/Blink browsers */
  ::-webkit-scrollbar {
    width: 8px;
    height: 8px;
  }
  ::-webkit-scrollbar-track {
    background: {scrollbar_track};
  }
  ::-webkit-scrollbar-thumb {
    background: {scrollbar_thumb};
    border-radius: 4px;
  }
  ::-webkit-scrollbar-thumb:hover {
    background: {scrollbar_thumb_hover};
  }
  body {
    font-family: 'Outfit', 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background-color: {bg_color};
    color: {text_color};
    margin: 0;
    padding: 0;
    font-size: 14px;
    line-height: 1.5;
    zoom: {zoom_level};
    width: {inverse_zoom_width};
    /* For IE11 / Shell.Explorer emulation scrollbar styling */
    scrollbar-face-color: {scrollbar_thumb};
    scrollbar-track-color: {scrollbar_track};
    scrollbar-arrow-color: {text_muted};
    scrollbar-shadow-color: {scrollbar_track};
    scrollbar-highlight-color: {scrollbar_track};
    scrollbar-3dlight-color: {scrollbar_track};
    scrollbar-darkshadow-color: {scrollbar_track};
    scrollbar-base-color: {scrollbar_track};
  }
  .container {
    padding: 16px;
    display: inline-block;
    min-width: 100%;
  }
  .section {
    background: {section_bg};
    border: 1px solid {section_border};
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 16px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
  }
  .section-title {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: {text_muted};
    margin-bottom: 8px;
    font-weight: 600;
  }
  .source-text {
    font-size: 16px;
    color: {text_color};
    line-height: 1.6;
    word-break: break-word;
    white-space: {source_white_space};
    -moz-user-select: none;
    -webkit-user-select: none;
    -ms-user-select: none;
    user-select: none;
  }
  .source-text span.word {
    cursor: pointer;
    transition: background-color 0.2s, color 0.2s;
    border-radius: 3px;
    padding: 0 2px;
  }
  .source-text span.word.flipped {
    background-color: {flipped_bg};
    color: {flipped_text};
    font-weight: 300;
    border: 1px dashed {flipped_border};
    padding: 0 3px;
    margin: 0 -1px;
    border-radius: 4px;
  }
  .source-text span.word:hover {
    background-color: {word_hover};
  }
  .source-text span.highlight-orange {
  }
  .source-text span.highlight-purple {
  }
  .source-text span.not-connected {
    background-color: {not_connected_bg};
    color: {not_connected_text};
    cursor: default;
  }
  .source-text span.not-connected:hover {
    background-color: {not_connected_bg};
  }
  .source-text span.word.highlight-orange-active {
    background-color: {highlight_orange_active_bg} !important;
    color: {highlight_orange_active_text} !important;
    text-decoration: none;
    border-color: {highlight_orange_active_text} !important;
  }
  .source-text span.word.highlight-orange-active:hover {
    background-color: {highlight_orange_active_hover_bg} !important;
    color: {highlight_orange_active_text} !important;
  }
  .source-text span.word.highlight-purple-active {
    background-color: {highlight_purple_active_bg} !important;
    color: {highlight_purple_active_text} !important;
    text-decoration: none;
    border-color: {highlight_purple_active_text} !important;
  }
  .source-text span.word.highlight-purple-active:hover {
    background-color: {highlight_purple_active_hover_bg} !important;
    color: {highlight_purple_active_text} !important;
  }
  .translation-text {
    font-size: 16px;
    color: {text_color};
    line-height: 1.6;
    word-break: break-word;
    white-space: {source_white_space};
  }
  table {
    width: 100%;
    border-collapse: collapse;
    margin-top: 8px;
    table-layout: auto;
  }
  #lemma-table th, #lemma-table td {
    width: 1%;
    white-space: nowrap;
    padding-right: 24px;
  }
  #lemma-table th:last-child, #lemma-table td:last-child {
    width: auto;
    padding-right: 12px;
  }
  .scrollable-cell {
    width: 100%;
    box-sizing: border-box;
    -ms-overflow-style: none;  /* IE and Edge */
    scrollbar-width: none;  /* Firefox */
  }
  .scrollable-cell::-webkit-scrollbar {
    display: none; /* Chrome, Safari and Opera */
  }
  /* When the window is NOT maximized (normally sized) */
  body:not(.maximized) {
    max-width: 100vw;
    overflow-x: hidden;
  }
  body:not(.maximized) .container {
    display: block;
    width: 100%;
    max-width: 100%;
  }
  body:not(.maximized) .section {
    max-width: 100%;
  }
  body:not(.maximized) #lemma-table {
    table-layout: fixed;
    width: 100%;
  }
  body:not(.maximized) #lemma-table th,
  body:not(.maximized) #lemma-table td {
    width: 18%;
    padding-right: 12px;
  }
  body:not(.maximized) #lemma-table th:last-child,
  body:not(.maximized) #lemma-table td:last-child {
    width: 28%;
    padding-right: 12px;
  }
  body:not(.maximized) .scrollable-cell {
    overflow-x: auto;
    white-space: nowrap;
    max-width: 100%;
  }
  /* When maximized */
  body.maximized .scrollable-cell {
    overflow-x: visible;
    white-space: normal;
    max-width: none;
  }
  th {
    text-align: left;
    padding: 10px 12px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: {text_muted};
    border-bottom: 1px solid {table_th_border};
    font-weight: 600;
  }
  td {
    padding: 10px 12px;
    border-bottom: 1px solid {table_border};
    color: {table_text};
    vertical-align: top;
  }
  tr:hover td {
    background: {row_hover};
  }
  tr.selected.highlight-orange td {
    background: {selected_orange_row_bg};
    color: {selected_orange_row_text};
  }
  tr.selected.highlight-purple td {
    background: {selected_purple_row_bg};
    color: {selected_purple_row_text};
  }
  .editable {
    cursor: pointer;
  }
  td.dirty {
    border-left: 3px solid #ff7b72;
  }
  .skeleton-loader {
    display: inline-block;
    height: 1.2em;
    width: 100%;
    background: linear-gradient(-90deg, {table_border} 0%, {table_th_border} 50%, {table_border} 100%);
    background-size: 400% 400%;
    animation: pulse-skeleton 1.5s ease infinite;
    border-radius: 4px;
    vertical-align: middle;
  }
  @keyframes pulse-skeleton {
    0% { background-position: 0% 50% }
    50% { background-position: 100% 50% }
    100% { background-position: 0% 50% }
  }
</style>
</head>
<body class="{theme_class}">
<div class="container">
  <div class="section">
    <div class="section-title">Source Text</div>
    <div class="source-text" id="source-container">{source_html}</div>
  </div>
  
  <div class="section">
    <div class="section-title">Translation</div>
    <div class="translation-text" id="translation-container">{sentence_html}</div>
  </div>
  
  <div class="section">
    <div class="section-title">Lemmas</div>
    <table id="lemma-table">
      <thead>
        {table_header_html}
      </thead>
      <tbody>
        {table_rows_html}
      </tbody>
    </table>
  </div>
</div>
<script id="token-map" type="application/json">
{token_manifest}
</script>
<script id="tsv-path" type="text/plain">{working_tsv_path}</script>
<script id="llm-filled" type="text/plain">{llm_filled_js}</script>
<script id="session-zid" type="text/plain">{zid}</script>
<script id="session-lang" type="text/plain">{language}</script>
<script id="display-mode" type="text/plain">{display_mode_js}</script>
<script id="auto-inject-updates" type="text/plain">{auto_inject_updates_js}</script>
<script id="run-enrichment" type="text/plain">{run_enrichment_js}</script>
<script id="worker-launched" type="text/plain">{worker_launched_js}</script>


<script type="text/javascript">
(function() {
    function addEvent(el, type, fn) {
        if (el.addEventListener) {
            el.addEventListener(type, fn, false);
        } else if (el.attachEvent) {
            el.attachEvent('on' + type, fn);
        } else {
            el['on' + type] = fn;
        }
    }

    function init() {
        var selectedRowIdsMap = {};
        var initialHighlights = {};
        var hasHighlightCol = false;
        var lastClickedRowId = null;
        var focusedRowId = null;
        var deltas = [];
        var historyStack = [];
        var historyIndex = -1;
        var touchedCells = {};
        var lastClickedCell = null;
        var lastHoveredCell = null;
        var isDragSelecting = false;
        var dragStartRowId = null;
        var dragSelectMode = true;
        var isTokenDragSelecting = false;
        var tokenDragMode = true;
        var dragOccurred = false;
        var justFinishedDrag = false;
        var tokenDragStartIdx = -1;
        var initialSelectedMap = null;
        var mousedownTargetSpan = null;
        var isRmbDragFlipping = false;
        var rmbFlipMode = true;
        var initialFlippedMap = null;
        
        var tokenMap = [];
        try {
            var tokenMapEl = document.getElementById('token-map');
            var jsonStr = tokenMapEl.text || tokenMapEl.textContent || tokenMapEl.innerHTML || "[]";
            tokenMap = JSON.parse(jsonStr);
        } catch(e) {}
        
        var isProgressive = false;
        try {
            var progEl = document.getElementById('display-mode');
            if (progEl && (progEl.textContent || progEl.innerText).trim() === 'progressive') {
                isProgressive = true;
            }
        } catch(e) {}
        
        window.receiveUpdate = function(data) {
            if (!data) return;
            
            if (data.stage === 'finished') {
                if (window.pollInterval) {
                    clearInterval(window.pollInterval);
                    window.pollInterval = null;
                }
                if (window.ahkCall) {
                    window.ahkCall('finished', '');
                }
            }
            
            var updated = false;
            if (data.sourceText) {
                var container = document.getElementById('source-container');
                if (container) {
                    var pendingNode = container.querySelector('[data-pending="true"]');
                    var hasSpans = container.querySelector('span.word') !== null;
                    if (pendingNode || !hasSpans) {
                        // Only apply when in skeleton/pending state or no spans rendered yet.
                        // Do NOT replace if spans already exist — wiping textContent destroys
                        // all span DOM nodes that MVPBookmark holds live references to.
                        var currentText = (container.textContent || container.innerText || "").trim().replace(/\\s+/g, ' ');
                        var newText = data.sourceText.trim().replace(/\\s+/g, ' ');
                        if (pendingNode || currentText !== newText) {
                            container.textContent = data.sourceText;
                            if (typeof tokenSpans !== 'undefined') {
                                tokenSpans = [];
                            }
                            updated = true;
                        }
                    }
                }
            }
            if (data.translatedText) {
                var container = document.getElementById('translation-container');
                if (container) {
                    var pendingNode = container.querySelector('[data-pending="true"]');
                    var currentText = (container.textContent || container.innerText || "").trim().replace(/\\s+/g, ' ');
                    var tempDiv = document.createElement('div');
                    tempDiv.innerHTML = data.translatedText;
                    var newText = (tempDiv.textContent || tempDiv.innerText || "").trim().replace(/\\s+/g, ' ');
                    if (pendingNode || currentText !== newText) {
                        container.innerHTML = data.translatedText;
                        updated = true;
                    }
                }
            }
            
            if (updated) {
                if (window.clearMVPBookmarks) window.clearMVPBookmarks();
                if (window.rebindMVPBookmarks) window.rebindMVPBookmarks();
            }
            
            var rowsData = null;
            if (data.stage) {
                if (data.rows) {
                    rowsData = data.rows;
                }
            } else {
                rowsData = data;
            }
            
            if (rowsData) {
                for (var rowId in rowsData) {
                    if (rowsData.hasOwnProperty(rowId)) {
                        var tr = document.querySelector('tr[data-row-id="' + rowId + '"]');
                        if (tr) {
                            var tds = tr.getElementsByTagName('td');
                            var rowData = rowsData[rowId];
                            if (tds.length >= 5) {
                                if (!tds[2].classList.contains('dirty') && rowData.hasOwnProperty('trans') && rowData.trans !== undefined) {
                                    var div = tds[2].querySelector('.scrollable-cell');
                                    var val = rowData.trans || "";
                                    var oldVal = div ? div.textContent : (tds[2].classList.contains('editing') ? null : tds[2].textContent);
                                    if (oldVal !== val) {
                                        if (div) div.textContent = val;
                                        else if (!tds[2].classList.contains('editing')) tds[2].textContent = val;
                                        updated = true;
                                    }
                                }
                                if (!tds[3].classList.contains('dirty') && rowData.hasOwnProperty('ipa') && rowData.ipa !== undefined) {
                                    var div = tds[3].querySelector('.scrollable-cell');
                                    var val = rowData.ipa || "";
                                    var oldVal = div ? div.textContent : (tds[3].classList.contains('editing') ? null : tds[3].textContent);
                                    if (oldVal !== val) {
                                        if (div) div.textContent = val;
                                        else if (!tds[3].classList.contains('editing')) tds[3].textContent = val;
                                        updated = true;
                                    }
                                }
                                if (!tds[4].classList.contains('dirty') && rowData.hasOwnProperty('morph') && rowData.morph !== undefined) {
                                    var div = tds[4].querySelector('.scrollable-cell');
                                    var val = rowData.morph || "";
                                    var oldVal = div ? div.innerHTML : (tds[4].classList.contains('editing') ? null : tds[4].innerHTML);
                                    if (oldVal !== val) {
                                        if (div) div.innerHTML = val;
                                        else if (!tds[4].classList.contains('editing')) tds[4].innerHTML = val;
                                        updated = true;
                                    }
                                }
                            }
                        }
                    }
                }
            }
            
            // Force IE11 layout reflow to fix table rendering glitches
            if (updated) {
                var table = document.getElementById('lemma-table');
                if (table) {
                    var scrollY = window.pageYOffset || document.documentElement.scrollTop || document.body.scrollTop;
                    var disp = table.style.display;
                    table.style.display = 'none';
                    table.offsetHeight; // trigger reflow
                    table.style.display = disp;
                    window.scrollTo(0, scrollY);
                }
            }
        };

        window.startPolling = function() {
            // Polling is now handled natively by AutoHotkey to prevent cursor flickering
            // and cross-drive 'Access is denied' errors in the MSHTML engine.
        };

        var workerLaunched = false;
        try {
            var wlEl = document.getElementById('worker-launched');
            if (wlEl && (wlEl.textContent || wlEl.innerText).trim() === 'true') {
                workerLaunched = true;
            }
        } catch(e) {}
        
        var sourceContainer = document.getElementById('source-container');
        var spans = sourceContainer ? sourceContainer.getElementsByTagName('span') : [];
        var tokenSpans = [];
        for (var i = 0; i < spans.length; i++) {
            if (spans[i].classList.contains('word')) {
                tokenSpans.push(spans[i]);
            }
        }
        
        
        function flipWord(span, toTranslation) {
            if (!span.getAttribute('data-original-text')) {
                span.setAttribute('data-original-text', span.textContent || span.innerText || "");
            }
            var isFlipped = span.classList.contains('flipped');
            if (toTranslation && !isFlipped) {
                var trans = getWordTranslation(span);
                if (trans) {
                    span.classList.add('flipped');
                    span.textContent = trans;
                }
            } else if (!toTranslation && isFlipped) {
                span.classList.remove('flipped');
                span.textContent = span.getAttribute('data-original-text');
            }
        }

        function findTokenData(span) {
            var wordIdx = parseInt(span.getAttribute('data-word-idx'));
            for (var i = 0; i < tokenMap.length; i++) {
                var t = tokenMap[i];
                if (t.visual_idx === wordIdx) {
                    return t;
                }
            }
            return null;
        }
        
        function getWordTranslation(span) {
            var tokenData = findTokenData(span);
            if (!tokenData || !tokenData.row_ids || tokenData.row_ids.length === 0) {
                return "";
            }
            var translations = [];
            for (var j = 0; j < tokenData.row_ids.length; j++) {
                var rowId = tokenData.row_ids[j];
                var tr = null;
                for (var k = 0; k < tableRows.length; k++) {
                    if (parseInt(tableRows[k].getAttribute('data-row-id')) === rowId) {
                        tr = tableRows[k];
                        break;
                    }
                }
                if (tr) {
                    var tds = tr.getElementsByTagName('td');
                    for (var m = 0; m < tds.length; m++) {
                        if (tds[m].getAttribute('data-col') === 'WordDestination') {
                            var trans = tds[m].textContent || tds[m].innerText || "";
                            trans = trans.trim();
                            if (trans && translations.indexOf(trans) === -1) {
                                translations.push(trans);
                            }
                        }
                    }
                }
            }
            return translations.join(', ');
        }
        
        for (var i = 0; i < tokenSpans.length; i++) {
            (function(span) {
                addEvent(span, 'mousedown', function(e) {
                    if (window.__selectableTextMode) return;
                    e = e || window.event;
                    
                    if (e.button === 0) { // LMB
                        var tokenData = findTokenData(span);
                        if (!tokenData || !tokenData.row_ids) return;
                        
                        isTokenDragSelecting = true;
                        dragOccurred = false;
                        mousedownTargetSpan = span;
                        
                        tokenDragStartIdx = -1;
                        for (var k = 0; k < tokenSpans.length; k++) {
                            if (tokenSpans[k] === span) {
                                tokenDragStartIdx = k;
                                break;
                            }
                        }
                        
                        initialSelectedMap = {};
                        for (var key in selectedRowIdsMap) {
                            if (selectedRowIdsMap.hasOwnProperty(key)) {
                                initialSelectedMap[key] = selectedRowIdsMap[key];
                            }
                        }
                        
                        var allSelected = true;
                        for (var j = 0; j < tokenData.row_ids.length; j++) {
                            if (!selectedRowIdsMap.hasOwnProperty(String(tokenData.row_ids[j]))) {
                                allSelected = false;
                                break;
                            }
                        }
                        
                        tokenDragMode = !allSelected;
                        
                        for (var j = 0; j < tokenData.row_ids.length; j++) {
                            if (tokenDragMode) {
                                selectedRowIdsMap[String(tokenData.row_ids[j])] = true;
                            } else {
                                delete selectedRowIdsMap[String(tokenData.row_ids[j])];
                            }
                        }
                        updateRowStyles();
                        updateBidirectionalHighlights();
                        
                        if (e.preventDefault) {
                            e.preventDefault();
                        } else {
                            e.returnValue = false;
                        }
                    } else if (e.button === 2) { // RMB
                        isRmbDragFlipping = true;
                        dragOccurred = false;
                        mousedownTargetSpan = span;
                        
                        tokenDragStartIdx = -1;
                        for (var k = 0; k < tokenSpans.length; k++) {
                            if (tokenSpans[k] === span) {
                                tokenDragStartIdx = k;
                                break;
                            }
                        }
                        
                        initialFlippedMap = [];
                        for (var k = 0; k < tokenSpans.length; k++) {
                            initialFlippedMap.push(tokenSpans[k].classList.contains('flipped'));
                        }
                        
                        rmbFlipMode = !span.classList.contains('flipped');
                        flipWord(span, rmbFlipMode);
                        
                        if (e.preventDefault) {
                            e.preventDefault();
                        } else {
                            e.returnValue = false;
                        }
                    }
                });
                
                addEvent(span, 'mouseover', function(e) {
                    if (window.__selectableTextMode) return;
                    e = e || window.event;
                    if (isTokenDragSelecting) {
                        if (e.buttons !== undefined && (e.buttons & 1) === 0) {
                            isTokenDragSelecting = false;
                            notifyAHKSelection();
                            return;
                        }
                        dragOccurred = true;
                        
                        var currIdx = -1;
                        for (var k = 0; k < tokenSpans.length; k++) {
                            if (tokenSpans[k] === span) {
                                currIdx = k;
                                break;
                            }
                        }
                        if (currIdx === -1 || tokenDragStartIdx === -1) return;
                        
                        selectedRowIdsMap = {};
                        for (var key in initialSelectedMap) {
                            if (initialSelectedMap.hasOwnProperty(key)) {
                                selectedRowIdsMap[key] = initialSelectedMap[key];
                            }
                        }
                        
                        var minIdx = Math.min(tokenDragStartIdx, currIdx);
                        var maxIdx = Math.max(tokenDragStartIdx, currIdx);
                        
                        for (var k = minIdx; k <= maxIdx; k++) {
                            var s = tokenSpans[k];
                            var td = findTokenData(s);
                            if (td && td.row_ids) {
                                for (var j = 0; j < td.row_ids.length; j++) {
                                    if (tokenDragMode) {
                                        selectedRowIdsMap[String(td.row_ids[j])] = true;
                                    } else {
                                        delete selectedRowIdsMap[String(td.row_ids[j])];
                                    }
                                }
                            }
                        }
                        updateRowStyles();
                        updateBidirectionalHighlights();
                    } else if (isRmbDragFlipping) {
                        dragOccurred = true;
                        
                        var currIdx = -1;
                        for (var k = 0; k < tokenSpans.length; k++) {
                            if (tokenSpans[k] === span) {
                                currIdx = k;
                                break;
                            }
                        }
                        if (currIdx === -1 || tokenDragStartIdx === -1) return;
                        
                        var minIdx = Math.min(tokenDragStartIdx, currIdx);
                        var maxIdx = Math.max(tokenDragStartIdx, currIdx);
                        
                        for (var k = 0; k < tokenSpans.length; k++) {
                            var s = tokenSpans[k];
                            var shouldFlip = initialFlippedMap[k];
                            if (k >= minIdx && k <= maxIdx) {
                                shouldFlip = rmbFlipMode;
                            }
                            flipWord(s, shouldFlip);
                        }
                    }
                });
            })(tokenSpans[i]);
        }
        
        var sourceContainer = document.getElementById('source-container');
        if (sourceContainer) {
            addEvent(sourceContainer, 'contextmenu', function(e) {
                e = e || window.event;
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                return false;
            });
        }
        
        var lemmaTable = document.getElementById('lemma-table');
        var tableRows = [];
        if (lemmaTable) {
            var tbodies = lemmaTable.getElementsByTagName('tbody');
            var rowsContainer = tbodies.length > 0 ? tbodies[0] : lemmaTable;
            var allRows = rowsContainer.getElementsByTagName('tr');
            for (var i = 0; i < allRows.length; i++) {
                if (allRows[i].getAttribute('data-row-id') !== null) {
                    tableRows.push(allRows[i]);
                }
            }
        }
        
        var hasHighlightCol = {has_highlight_col};

        for (var i = 0; i < tableRows.length; i++) {
            var row = tableRows[i];
            var rowIdStr = String(row.getAttribute('data-row-id'));
            var isHighlighted = false;
            if (hasHighlightCol && row.getAttribute('data-selected') === '1') {
                isHighlighted = true;
            }
            initialHighlights[rowIdStr] = isHighlighted;
            if (isHighlighted) {
                selectedRowIdsMap[rowIdStr] = true;
            }
        }
        updateRowStyles();
        updateBidirectionalHighlights();
        
        for (var i = 0; i < tableRows.length; i++) {
            (function(row) {
                addEvent(row, 'mousedown', function(e) {
                    if (window.__selectableTextMode) return;
                    e = e || window.event;
                    var target = e.target || e.srcElement;
                    if (target && target.tagName === 'INPUT') {
                        return;
                    }
                    if (e.button !== 0) {
                        return;
                    }
                    var rowId = parseInt(row.getAttribute('data-row-id'));
                    var rowIdStr = String(rowId);
                    
                    isDragSelecting = true;
                    dragOccurred = false;
                    
                    if (e.shiftKey && lastClickedRowId !== null) {
                        dragStartRowId = lastClickedRowId;
                        dragSelectMode = true;
                        
                        initialSelectedMap = {};
                        for (var key in selectedRowIdsMap) {
                            if (selectedRowIdsMap.hasOwnProperty(key)) {
                                initialSelectedMap[key] = selectedRowIdsMap[key];
                            }
                        }
                        
                        var start = Math.min(parseInt(lastClickedRowId), parseInt(rowId));
                        var end = Math.max(parseInt(lastClickedRowId), parseInt(rowId));
                        for (var j = start; j <= end; j++) {
                            selectedRowIdsMap[String(j)] = true;
                        }
                        lastClickedRowId = rowId;
                    } else {
                        dragStartRowId = rowId;
                        
                        initialSelectedMap = {};
                        for (var key in selectedRowIdsMap) {
                            if (selectedRowIdsMap.hasOwnProperty(key)) {
                                initialSelectedMap[key] = selectedRowIdsMap[key];
                            }
                        }
                        
                        if (selectedRowIdsMap.hasOwnProperty(rowIdStr)) {
                            delete selectedRowIdsMap[rowIdStr];
                            dragSelectMode = false;
                        } else {
                            selectedRowIdsMap[rowIdStr] = true;
                            dragSelectMode = true;
                        }
                        lastClickedRowId = rowId;
                    }
                    
                    focusedRowId = rowId;
                    updateRowStyles();
                    updateBidirectionalHighlights();
                    
                    if (e.preventDefault) {
                        e.preventDefault();
                    } else {
                        e.returnValue = false;
                    }
                });
                
                addEvent(row, 'mouseover', function(e) {
                    if (window.__selectableTextMode) return;
                    e = e || window.event;
                    if (isDragSelecting) {
                        if (e.buttons !== undefined && (e.buttons & 1) === 0) {
                            isDragSelecting = false;
                            notifyAHKSelection();
                            return;
                        }
                        dragOccurred = true;
                        var rowId = parseInt(row.getAttribute('data-row-id'));
                        
                        // Reset to the state before the current drag gesture started
                        selectedRowIdsMap = {};
                        for (var key in initialSelectedMap) {
                            if (initialSelectedMap.hasOwnProperty(key)) {
                                selectedRowIdsMap[key] = initialSelectedMap[key];
                            }
                        }
                        
                        // Apply the drag selection range from dragStartRowId to current rowId
                        var start = Math.min(dragStartRowId, rowId);
                        var end = Math.max(dragStartRowId, rowId);
                        for (var j = start; j <= end; j++) {
                            var rIdStr = String(j);
                            if (dragSelectMode) {
                                selectedRowIdsMap[rIdStr] = true;
                            } else {
                                delete selectedRowIdsMap[rIdStr];
                            }
                        }
                        
                        focusedRowId = rowId;
                        updateRowStyles();
                        updateBidirectionalHighlights();
                    }
                });
                
                var tds = row.getElementsByTagName('td');
                for (var j = 0; j < tds.length; j++) {
                    if (tds[j].classList.contains('editable')) {
                        (function(cell) {
                            addEvent(cell, 'click', function(e) {
                                if (window.__selectableTextMode) return;
                                lastClickedCell = cell;
                            });
                            addEvent(cell, 'mouseover', function(e) {
                                if (window.__selectableTextMode) return;
                                lastHoveredCell = cell;
                            });
                            addEvent(cell, 'mouseout', function(e) {
                                if (lastHoveredCell === cell) {
                                    lastHoveredCell = null;
                                }
                            });
                            addEvent(cell, 'dblclick', function() {
                                if (window.__selectableTextMode) return;
                                makeEditable(cell);
                            });
                        })(tds[j]);
                    }
                }
            })(tableRows[i]);
        }
        
        addEvent(document, 'mouseup', function(e) {
            e = e || window.event;
            var needNotify = false;
            if (isDragSelecting || isTokenDragSelecting || isRmbDragFlipping) {
                if (dragOccurred) {
                    justFinishedDrag = true;
                    setTimeout(function() {
                        justFinishedDrag = false;
                    }, 50);
                }
                isDragSelecting = false;
                isTokenDragSelecting = false;
                isRmbDragFlipping = false;
                needNotify = true;
            }
            mousedownTargetSpan = null;
            if (needNotify) {
                notifyAHKSelection();
            }
        });
        
        addEvent(document, 'contextmenu', function(e) {
            if (justFinishedDrag) {
                e = e || window.event;
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                return false;
            }
        });
        
        addEvent(document, 'keydown', function(e) {
            e = e || window.event;
            var activeEl = document.activeElement;
            if (activeEl && activeEl.tagName === 'INPUT') return;
            
            var keyCode = e.keyCode;
            if (e.ctrlKey && keyCode === 90) { // Ctrl+Z
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (window.undo) window.undo();
                return;
            } else if (e.ctrlKey && keyCode === 89) { // Ctrl+Y
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (window.redo) window.redo();
                return;
            } else if (e.ctrlKey && keyCode === 65) { // Ctrl+A
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (typeof tableRows !== 'undefined' && tableRows.length > 0) {
                    for (var i = 0; i < tableRows.length; i++) {
                        var rowId = String(tableRows[i].getAttribute('data-row-id'));
                        selectedRowIdsMap[rowId] = true;
                    }
                    updateRowStyles();
                    updateBidirectionalHighlights();
                    if (typeof notifyAHKSelection !== 'undefined') {
                        notifyAHKSelection();
                    }
                }
                return;
            }
            if (keyCode === 27) { // Escape key
                clearAllSelections();
                updateBidirectionalHighlights();
                notifyAHKSelection();
                return;
            }
            if (keyCode === 40 || keyCode === 38) { // ArrowDown or ArrowUp
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (tableRows.length === 0) return;
                
                if (focusedRowId === null) {
                    focusedRowId = 0;
                } else {
                    if (keyCode === 40) {
                        focusedRowId = Math.min(focusedRowId + 1, tableRows.length - 1);
                    } else {
                        focusedRowId = Math.max(focusedRowId - 1, 0);
                    }
                }
                updateRowFocus();
            } else if (keyCode === 46) { // Delete
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (window.deleteSelectedRows) {
                    window.deleteSelectedRows();
                }
            } else if (keyCode === 32) { // Space
                if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                if (focusedRowId !== null) {
                    if (selectedRowIdsMap.hasOwnProperty(focusedRowId)) {
                        delete selectedRowIdsMap[focusedRowId];
                    } else {
                        selectedRowIdsMap[focusedRowId] = true;
                    }
                    lastClickedRowId = focusedRowId;
                    updateRowStyles();
                    updateBidirectionalHighlights();
                    notifyAHKSelection();
                }
            } else if (keyCode === 113) { // F2
                var cellToEdit = null;
                if (lastHoveredCell) {
                    var rId = parseInt(lastHoveredCell.parentElement.getAttribute('data-row-id'));
                    if (rId === focusedRowId) {
                        cellToEdit = lastHoveredCell;
                    }
                }
                if (!cellToEdit && lastClickedCell) {
                    var rId = parseInt(lastClickedCell.parentElement.getAttribute('data-row-id'));
                    if (rId === focusedRowId) {
                        cellToEdit = lastClickedCell;
                    }
                }
                if (!cellToEdit && focusedRowId !== null) {
                    var activeRow = null;
                    for (var k = 0; k < tableRows.length; k++) {
                        if (tableRows[k].getAttribute('data-row-id') == focusedRowId) {
                            activeRow = tableRows[k];
                            break;
                        }
                    }
                    if (activeRow) {
                        var tds = activeRow.getElementsByTagName('td');
                        for (var k = 0; k < tds.length; k++) {
                            if (tds[k].classList.contains('editable')) {
                                cellToEdit = tds[k];
                                break;
                            }
                        }
                    }
                }
                if (cellToEdit) {
                    makeEditable(cellToEdit);
                }
            }
        });
        
        addEvent(document, 'click', function(e) {
            if (justFinishedDrag) {
                justFinishedDrag = false;
                return;
            }
        });
        
        function clearAllSelections() {
            selectedRowIdsMap = {};
            lastClickedRowId = null;
            updateRowStyles();
            updateBidirectionalHighlights();
        }
        
        window.clearAllSelectionsAndNotify = function() {
            clearAllSelections();
            notifyAHKSelection();
        };
        
        function toggleRowSelection(rowId, forceState) {
            var rIdStr = String(rowId);
            if (forceState) {
                selectedRowIdsMap[rIdStr] = true;
            } else {
                if (selectedRowIdsMap.hasOwnProperty(rIdStr)) {
                    delete selectedRowIdsMap[rIdStr];
                } else {
                    selectedRowIdsMap[rIdStr] = true;
                }
            }
            updateRowStyles();
        }
        
        function updateRowStyles() {
            for (var i = 0; i < tableRows.length; i++) {
                var row = tableRows[i];
                var rowIdStr = String(row.getAttribute('data-row-id'));
                if (selectedRowIdsMap.hasOwnProperty(rowIdStr)) {
                    row.classList.add('selected');
                } else {
                    row.classList.remove('selected');
                }
            }
        }
        
        function updateRowFocus() {
            for (var i = 0; i < tableRows.length; i++) {
                var row = tableRows[i];
                var rowId = parseInt(row.getAttribute('data-row-id'));
                if (rowId === focusedRowId) {
                    row.style.outline = '1px solid #58a6ff';
                    row.scrollIntoView({ block: 'nearest' });
                } else {
                    row.style.outline = 'none';
                }
            }
        }
        
        function updateBidirectionalHighlights() {
            for (var i = 0; i < tokenSpans.length; i++) {
                var span = tokenSpans[i];
                try {
                    span.classList.remove('highlight-orange-active');
                    span.classList.remove('highlight-purple-active');
                } catch(e) {}
            }
            
            for (var rId in selectedRowIdsMap) {
                if (!selectedRowIdsMap.hasOwnProperty(rId)) continue;
                var rowId = parseInt(rId);
                for (var i = 0; i < tokenMap.length; i++) {
                    var token = tokenMap[i];
                    if (token.row_ids && token.row_ids.indexOf(rowId) !== -1) {
                        var span = null;
                        for (var k = 0; k < tokenSpans.length; k++) {
                            if (tokenSpans[k].getAttribute('data-word-idx') == token.visual_idx) {
                                span = tokenSpans[k];
                                break;
                            }
                        }
                        if (span) {
                            try {
                                if (span.classList.contains('highlight-purple')) {
                                    span.classList.add('highlight-purple-active');
                                } else if (span.classList.contains('highlight-orange')) {
                                    span.classList.add('highlight-orange-active');
                                }
                            } catch(e) {}
                        }
                    }
                }
            }
        }
        
        function getSelectedRowsArray() {
            var arr = [];
            for (var k in selectedRowIdsMap) {
                if (selectedRowIdsMap.hasOwnProperty(k)) {
                    arr.push(parseInt(k));
                }
            }
            return arr;
        }
        
        function notifyAHKSelection() {
            if (window.ahkCall) {
                window.ahkCall('selection', getSelectedRowsArray().join(','));
                window.ahkCall('dirty', window.isDirty() ? 'true' : 'false');
            }
        }
        
        function makeEditable(cell) {
            if (cell.getElementsByTagName('input').length > 0) return;
            
            var scrollDiv = cell.querySelector('.scrollable-cell');
            var originalValue = scrollDiv ? (scrollDiv.textContent || scrollDiv.innerText) : (cell.textContent || cell.innerText || "");
            var colName = cell.getAttribute('data-col');
            var rowId = cell.parentElement.getAttribute('data-row-id');
            
            var input = document.createElement('input');
            input.type = 'text';
            input.className = 'edit-input';
            input.value = originalValue;
            input.style.width = '100%';
            input.style.boxSizing = 'border-box';
            input.style.background = '{input_bg}';
            input.style.color = '{text_color}';
            input.style.border = '1px solid {input_border}';
            input.style.borderRadius = '4px';
            input.style.padding = '4px';
            
            cell.innerHTML = '';
            if (!cell.classList.contains('editing')) {
                cell.classList.add('editing');
            }
            cell.appendChild(input);
            input.focus();
            try {
                input.select();
            } catch(e) {}
            
            window.cancelActiveEdit = function() {
                cell.innerHTML = '';
                var div = document.createElement('div');
                div.className = 'scrollable-cell';
                div.appendChild(document.createTextNode(originalValue));
                cell.appendChild(div);
                cell.classList.remove('editing');
                window.cancelActiveEdit = null;
            };
            
            function commit() {
                var newValue = input.value;
                cell.innerHTML = '';
                var div = document.createElement('div');
                div.className = 'scrollable-cell';
                div.appendChild(document.createTextNode(newValue));
                cell.appendChild(div);
                cell.classList.remove('editing');
                window.cancelActiveEdit = null;
                if (newValue !== originalValue) {
                    var action = {
                        type: 'edit',
                        rowId: parseInt(rowId),
                        column: colName,
                        oldValue: originalValue,
                        newValue: newValue,
                        cell: cell
                    };
                    pushHistory(action);
                    rebuildDeltas();
                    touchedCells[rowId + '_' + colName] = true;
                }
            }
            window.commitActiveEdit = commit;
            
            addEvent(input, 'keydown', function(e) {
                e = e || window.event;
                var keyCode = e.keyCode;
                if (e.ctrlKey && keyCode === 65) { // Ctrl+A
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    input.select();
                } else if (keyCode === 13) { // Enter
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    commit();
                } else if (keyCode === 27) { // Escape
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    if (window.cancelActiveEdit) window.cancelActiveEdit();
                } else if (keyCode === 9) { // Tab
                    if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
                    commit();
                    
                    var tds = document.getElementsByTagName('td');
                    var editables = [];
                    for (var k = 0; k < tds.length; k++) {
                        if (tds[k].classList.contains('editable')) {
                            editables.push(tds[k]);
                        }
                    }
                    
                    var idx = -1;
                    for (var k = 0; k < editables.length; k++) {
                        if (editables[k] === cell) {
                            idx = k;
                            break;
                        }
                    }
                    var nextIdx = e.shiftKey ? idx - 1 : idx + 1;
                    if (nextIdx >= 0 && nextIdx < editables.length) {
                        makeEditable(editables[nextIdx]);
                    }
                }
            });
            
            addEvent(input, 'blur', function() {
                commit();
            });
        }
        
        window.getSelectedRows = function() {
            return JSON.stringify(getSelectedRowsArray());
        };
        
        window.setSelectedRows = function(rowsJsonStr) {
            selectedRowIdsMap = {};
            try {
                var arr = JSON.parse(rowsJsonStr);
                for (var k = 0; k < arr.length; k++) {
                    selectedRowIdsMap[String(arr[k])] = true;
                }
            } catch(e) {}
            updateRowStyles();
            updateBidirectionalHighlights();
        };
        
        window.deleteSelectedRows = function() {
            var selected = getSelectedRowsArray();
            if (selected.length === 0) return;
            var action = {
                type: 'delete',
                rowIds: selected
            };
            pushHistory(action);
            applyAction(action);
            rebuildDeltas();
        };
        
        function pushHistory(action) {
            historyStack.splice(historyIndex + 1);
            historyStack.push(action);
            historyIndex++;
        }
        
        function applyAction(action) {
            if (action.type === 'edit') {
                action.cell.innerHTML = '';
                var div = document.createElement('div');
                div.className = 'scrollable-cell';
                div.appendChild(document.createTextNode(action.newValue));
                action.cell.appendChild(div);
            } else if (action.type === 'delete') {
                for (var j = 0; j < action.rowIds.length; j++) {
                    var rId = action.rowIds[j];
                    for (var i = 0; i < tableRows.length; i++) {
                        if (parseInt(tableRows[i].getAttribute('data-row-id')) === rId) {
                            tableRows[i].style.display = 'none';
                            break;
                        }
                    }
                }
                clearAllSelections();
            }
        }
        
        function revertAction(action) {
            if (action.type === 'edit') {
                action.cell.innerHTML = '';
                var div = document.createElement('div');
                div.className = 'scrollable-cell';
                div.appendChild(document.createTextNode(action.oldValue));
                action.cell.appendChild(div);
            } else if (action.type === 'delete') {
                for (var j = 0; j < action.rowIds.length; j++) {
                    var rId = action.rowIds[j];
                    for (var i = 0; i < tableRows.length; i++) {
                        if (parseInt(tableRows[i].getAttribute('data-row-id')) === rId) {
                            tableRows[i].style.display = '';
                            break;
                        }
                    }
                }
            }
        }
        
        window.undo = function() {
            if (historyIndex < 0) return;
            var action = historyStack[historyIndex];
            historyIndex--;
            revertAction(action);
            rebuildDeltas();
        };
        
        window.redo = function() {
            if (historyIndex >= historyStack.length - 1) return;
            historyIndex++;
            var action = historyStack[historyIndex];
            applyAction(action);
            rebuildDeltas();
        };
        
        function rebuildDeltas() {
            deltas = [];
            var tds = document.getElementsByTagName('td');
            for (var k = 0; k < tds.length; k++) {
                tds[k].classList.remove('dirty');
            }
            
            for (var i = 0; i <= historyIndex; i++) {
                var action = historyStack[i];
                if (action.type === 'edit') {
                    var found = false;
                    for (var k = 0; k < deltas.length; k++) {
                        if (deltas[k].row_id === action.rowId && deltas[k].column === action.column) {
                            deltas[k].value = action.newValue;
                            found = true;
                            break;
                        }
                    }
                    if (!found) {
                        deltas.push({ row_id: action.rowId, column: action.column, value: action.newValue });
                    }
                } else if (action.type === 'delete') {
                    for (var j = 0; j < action.rowIds.length; j++) {
                        deltas.push({ row_id: action.rowIds[j], column: '_delete', value: true });
                    }
                }
            }
            
            for (var k = 0; k < deltas.length; k++) {
                var d = deltas[k];
                if (d.column !== '_delete') {
                    for (var j = 0; j < tableRows.length; j++) {
                        if (parseInt(tableRows[j].getAttribute('data-row-id')) === d.row_id) {
                            var tdst = tableRows[j].getElementsByTagName('td');
                            for (var m = 0; m < tdst.length; m++) {
                                if (tdst[m].getAttribute('data-col') === d.column) {
                                    if (!tdst[m].classList.contains('dirty')) {
                                        tdst[m].classList.add('dirty');
                                    }
                                    break;
                                }
                            }
                            break;
                        }
                    }
                }
            }
            
            if (window.ahkCall) {
                window.ahkCall('dirty', deltas.length > 0 ? 'true' : 'false');
            }
        }
        
        window.getDeltas = function() {
            var mergedDeltas = [];
            for (var i = 0; i < deltas.length; i++) {
                mergedDeltas.push(deltas[i]);
            }
            if (hasHighlightCol) {
                for (var i = 0; i < tableRows.length; i++) {
                    var row = tableRows[i];
                    var rowIdStr = String(row.getAttribute('data-row-id'));
                    var currentlySelected = selectedRowIdsMap.hasOwnProperty(rowIdStr);
                    var initiallySelected = initialHighlights[rowIdStr] || false;
                    if (currentlySelected !== initiallySelected) {
                        mergedDeltas.push({
                            row_id: parseInt(rowIdStr),
                            column: '{selected_col_name}',
                            value: currentlySelected ? '1' : ''
                        });
                    }
                }
            }
            return JSON.stringify(mergedDeltas);
        };
        
        window.clearDirty = function() {
            historyStack = [];
            historyIndex = -1;
            deltas = [];
            if (hasHighlightCol) {
                for (var i = 0; i < tableRows.length; i++) {
                    var row = tableRows[i];
                    var rowIdStr = String(row.getAttribute('data-row-id'));
                    initialHighlights[rowIdStr] = selectedRowIdsMap.hasOwnProperty(rowIdStr);
                }
            }
            var tds = document.getElementsByTagName('td');
            for (var k = 0; k < tds.length; k++) {
                tds[k].classList.remove('dirty');
            }
            if (window.ahkCall) {
                window.ahkCall('dirty', 'false');
            }
        };
        
        window.isDirty = function() {
            if (deltas.length > 0) return true;
            if (hasHighlightCol) {
                for (var i = 0; i < tableRows.length; i++) {
                    var row = tableRows[i];
                    var rowIdStr = String(row.getAttribute('data-row-id'));
                    var currentlySelected = selectedRowIdsMap.hasOwnProperty(rowIdStr);
                    var initiallySelected = initialHighlights[rowIdStr] || false;
                    if (currentlySelected !== initiallySelected) {
                        return true;
                    }
                }
            }
            return false;
        };
        
        window.editFocusedCell = function() {
            var cellToEdit = null;
            if (lastHoveredCell) {
                var rId = parseInt(lastHoveredCell.parentElement.getAttribute('data-row-id'));
                if (rId === focusedRowId) {
                    cellToEdit = lastHoveredCell;
                }
            }
            if (!cellToEdit && lastClickedCell) {
                var rId = parseInt(lastClickedCell.parentElement.getAttribute('data-row-id'));
                if (rId === focusedRowId) {
                    cellToEdit = lastClickedCell;
                }
            }
            if (!cellToEdit && focusedRowId !== null) {
                for (var k = 0; k < tableRows.length; k++) {
                    if (tableRows[k].getAttribute('data-row-id') == focusedRowId) {
                        var tds = tableRows[k].getElementsByTagName('td');
                        for (var j = 0; j < tds.length; j++) {
                            if (tds[j].classList.contains('editable')) {
                                cellToEdit = tds[j];
                                break;
                            }
                        }
                        break;
                    }
                }
            }
            if (cellToEdit) {
                makeEditable(cellToEdit);
            }
        };
        
        window.selectAllInActiveEdit = function() {
            var el = document.activeElement;
            if (el && el.tagName === 'INPUT') {
                el.select();
            }
        };
        
        window.copySelection = function() {
            try {
                document.execCommand('copy');
            } catch(e) {}
        };
    }

    function handleHorizontalScroll(e) {
        e = e || window.event;
        if (e.shiftKey || e.altKey) {
            var delta = e.wheelDelta ? -e.wheelDelta : (e.detail ? e.detail * 40 : 0);
            
            var target = e.target || e.srcElement;
            var scrollCell = null;
            var curr = target;
            while (curr) {
                if (curr.classList && curr.classList.contains('scrollable-cell')) {
                    scrollCell = curr;
                    break;
                }
                curr = curr.parentNode;
            }
            
            if (scrollCell) {
                scrollCell.scrollLeft += delta;
            } else {
                var scrollEl = document.documentElement || document.body;
                scrollEl.scrollLeft += delta;
            }
            
            if (e.preventDefault) { e.preventDefault(); } else { e.returnValue = false; }
            return false;
        }
    }
    addEvent(document, 'mousewheel', handleHorizontalScroll);
    addEvent(document, 'DOMMouseScroll', handleHorizontalScroll);

    if (window.addEventListener) {
        window.addEventListener('load', init, false);
    } else if (window.attachEvent) {
        window.attachEvent('onload', init);
    } else {
        window.onload = init;
    }
})();
</script>
</body>
</html>
"""
    # zoom_level is now passed as an argument
    
    try:
        numeric_zoom = float(zoom_level.replace('%', ''))
        inverse_width = f"{10000 / numeric_zoom:.3f}%"
    except Exception:
        inverse_width = "100%"

    if zoom_level.isdigit():
        zoom_level = f"{zoom_level}%"
        
    html_page = html_page.replace("{zoom_level}", zoom_level)
    html_page = html_page.replace("{inverse_zoom_width}", inverse_width)
    html_page = html_page.replace("{source_html}", source_html)
    html_page = html_page.replace("{sentence_html}", sentence_html)
    html_page = html_page.replace("{table_header_html}", table_header_html)
    html_page = html_page.replace("{table_rows_html}", table_rows_html)
    html_page = html_page.replace("{token_manifest}", json.dumps(token_manifest))
    html_page = html_page.replace("{working_tsv_path}", str(working_tsv_path))
    html_page = html_page.replace("{llm_filled_js}", "true" if llm_filled else "false")
    html_page = html_page.replace("{zid}", zid)
    html_page = html_page.replace("{display_mode_js}", "progressive" if is_progressive else "monolithic")
    html_page = html_page.replace("{auto_inject_updates_js}", "true" if auto_inject_updates else "false")
    html_page = html_page.replace("{run_enrichment_js}", run_enrich)
    html_page = html_page.replace("{worker_launched_js}", "true" if worker_launched else "false")

    html_page = html_page.replace("{language}", language)
    html_page = html_page.replace("{theme_class}", f"theme-{theme}")
    html_page = html_page.replace("{source_white_space}", "pre-wrap" if eff_mode == "multi" else "normal")
    
    selected_col_name = role_fields.get('selected', 'DeskSelected')
    html_page = html_page.replace("{selected_col_name}", selected_col_name)
    html_page = html_page.replace("{has_highlight_col}", "true" if col_highlighted != -1 else "false")

    theme = theme.lower()
    if theme in ("light", "white"):
        theme_colors = {'bg_color': '#f6f8fa', 'text_color': '#24292f', 'section_bg': '#ffffff', 'section_border': '#d0d7de', 'text_muted': '#57606a', 'table_border': '#d8dee4', 'table_th_border': '#d0d7de', 'table_text': '#24292f', 'row_hover': '#f3f4f6', 'word_hover': 'rgba(0, 0, 0, 0.05)', 'highlight_orange_active_bg': '#ffe169', 'highlight_orange_active_text': '#24292f', 'highlight_orange_active_hover_bg': '#ffd33d', 'highlight_purple_active_bg': '#dcd0ff', 'highlight_purple_active_text': '#24292f', 'highlight_purple_active_hover_bg': '#b89bf8', 'selected_orange_row_bg': 'rgba(255, 225, 105, 0.3)', 'selected_orange_row_text': '#b07e00', 'selected_purple_row_bg': 'rgba(220, 208, 255, 0.3)', 'selected_purple_row_text': '#6f42c1', 'flipped_bg': 'rgba(56, 166, 255, 0.15)', 'flipped_text': '#0969da', 'flipped_border': 'rgba(9, 105, 218, 0.6)', 'input_bg': '#ffffff', 'input_border': '#0969da', 'scrollbar_track': '#f6f8fa', 'scrollbar_thumb': '#d0d7de', 'scrollbar_thumb_hover': '#afb8c1', 'not_connected_bg': 'rgba(175, 184, 193, 0.15)', 'not_connected_text': '#57606a'}
    else:
        theme_colors = {'bg_color': '#0d0f12', 'text_color': '#e3e6eb', 'section_bg': 'rgba(255, 255, 255, 0.03)', 'section_border': 'rgba(255, 255, 255, 0.08)', 'text_muted': '#8b949e', 'table_border': 'rgba(255, 255, 255, 0.05)', 'table_th_border': 'rgba(255, 255, 255, 0.1)', 'table_text': '#c9d1d9', 'row_hover': 'rgba(255, 255, 255, 0.02)', 'word_hover': 'rgba(255, 255, 255, 0.1)', 'highlight_orange_active_bg': '#ffcc00', 'highlight_orange_active_text': '#0d0f12', 'highlight_orange_active_hover_bg': '#e6b800', 'highlight_purple_active_bg': '#9370db', 'highlight_purple_active_text': '#ffffff', 'highlight_purple_active_hover_bg': '#7b59c4', 'selected_orange_row_bg': 'rgba(255, 204, 0, 0.15)', 'selected_orange_row_text': '#ffcc00', 'selected_purple_row_bg': 'rgba(147, 112, 219, 0.15)', 'selected_purple_row_text': '#b39ddb', 'flipped_bg': 'rgba(56, 166, 255, 0.22)', 'flipped_text': '#a5d6ff', 'flipped_border': 'rgba(165, 214, 255, 0.6)', 'input_bg': '#1c1f24', 'input_border': '#58a6ff', 'scrollbar_track': '#0d0f12', 'scrollbar_thumb': '#30363d', 'scrollbar_thumb_hover': '#8b949e', 'not_connected_bg': 'rgba(139, 148, 158, 0.15)', 'not_connected_text': '#8b949e'}

    for key, val in theme_colors.items():
        html_page = html_page.replace('{' + key + '}', val)

    
    return html_page

def run_lookup_flow(text, language, target_lang, fmt, config, resolved_paths, goldendict, zid, text_mode='single'):
    import hashlib
    import time
    
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    kw_config = load_kardenwort_config(kardenwort_workspace)
    results_dir_name = kw_config.get('project_structure', 'generated_results_dir', fallback='results')
    results_dir = (kardenwort_workspace / results_dir_name).resolve()
    
    slug = generate_slug(text)
    cache_key = f"{zid}-{slug}.{language}.tsv"
    
    working_tsv_path = results_dir / cache_key
    
    ttl_seconds = goldendict['lookup_ttl_seconds']
    run_intellifiller = goldendict['run_intellifiller']
    
    main_text_provider = config.get('pipeline', 'text_base_provider', fallback=config.get('pipeline', 'lemma_base_provider', fallback='google'))
    try:
        sentence_translations = translate_source_text(text, language, target_lang, text_mode, config, resolved_paths, main_text_provider)
    except TranslationAlignmentError as tae:
        logger.error(f"Lookup translation alignment error: {tae}")
        sentence_translations = tae.partial_dict
        
    cached = False
    import re
    if ttl_seconds > 0:
        for cached_file in results_dir.glob(f"*-{slug}.{language}.tsv"):
            if cached_file.is_file():
                if (time.time() - cached_file.stat().st_mtime) <= ttl_seconds:
                    working_tsv_path = cached_file
                    cached = True
                    break
            
    if not cached:
        working_tsv_path = prepare_lookup_tsv(text, language, target_lang, config, resolved_paths, zid, ttl_seconds=0, cache_key=cache_key, text_mode=text_mode)
        
    comments, headers, data_rows = load_tsv_rows(working_tsv_path)
    mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
    role_fields = get_role_fields(mapping, headers)
    
    col_index = headers.index('SentenceSourceIndex') if 'SentenceSourceIndex' in headers else -1
    col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
    
    sentence_translation = resolve_translations(
        text, text_mode, data_rows, col_index, col_sentence_dest,
        sentence_translations, working_tsv_path, comments, headers,
        persist=True, return_single=True
    )
    
    save_translation_text = config.getboolean('settings', 'save_translation_text', fallback=False)
    translation_text_path = results_dir / f"{zid}-{slug}.{target_lang}.txt"
    eff_mode = _effective_text_mode(text, text_mode)
    _write_translation_txt(text, eff_mode, sentence_translations, translation_text_path, save_flag=save_translation_text, overwrite=True)
    
    col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields else -1
    col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields else -1
    
    if col_lemma != -1 and col_word_dest != -1:
        lemmas_provider = config.get('pipeline', 'lemma_reprocess_provider', fallback='intellifiller')
        lemmas_to_translate = []
        for row in data_rows:
            if len(row) > col_lemma and row[col_lemma].strip():
                if len(row) <= col_word_dest or not row[col_word_dest].strip():
                    lemmas_to_translate.append(row[col_lemma].strip())
        
        if lemmas_to_translate:
            unique_lemmas = list(dict.fromkeys(lemmas_to_translate))
            translations = translate_lemmas_fast_path(unique_lemmas, language, target_lang, config, resolved_paths, lemmas_provider)
            
            for row in data_rows:
                if len(row) > col_lemma and row[col_lemma].strip():
                    lemma = row[col_lemma].strip()
                    if len(row) <= col_word_dest or not row[col_word_dest].strip():
                        trans = translations.get(lemma, "")
                        while len(row) <= col_word_dest:
                            row.append("")
                        row[col_word_dest] = trans
            
            with file_lock(working_tsv_path):
                save_tsv_rows_safely(working_tsv_path, comments, headers, data_rows)

    if run_intellifiller:
        prompt_name = config.get('languages', f'{language}_prompt', fallback='')
        run_headless_intellifiller(working_tsv_path, prompt_name, config, resolved_paths)
        
    comments, headers, data_rows = load_tsv_rows(working_tsv_path)
    
    if not run_intellifiller:
        mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
        role_fields = {role: field for field, role in mapping['desk_columns'].items() if field in headers}
        
        cols_to_remove = []
        if 'morphology' in role_fields:
            cols_to_remove.append(role_fields['morphology'])
        elif 'WordSourceMorphologyAI' in headers:
            cols_to_remove.append('WordSourceMorphologyAI')
            
        if 'ipa' in role_fields:
            cols_to_remove.append(role_fields['ipa'])
        elif 'WordSourceIPA' in headers:
            cols_to_remove.append('WordSourceIPA')
            
        for col_name in cols_to_remove:
            if col_name in headers:
                col_idx = headers.index(col_name)
                for row in data_rows:
                    if len(row) > col_idx:
                        row[col_idx] = ""
                        
    return comments, headers, data_rows, sentence_translation

def render_section(token, ctx):
    import re
    html_output = ""
    
    def make_heading(heading_key, default_text):
        h_text = ctx.get('headings', {}).get(heading_key, "")
        if h_text == '__default__':
            h_text = default_text
        if h_text:
            return f"<h3>{h_text}</h3>\n"
        return ""
        
    if token == "source":
        html_output += make_heading("source", "Source Text")
        safe_text = ctx["text"].replace('\r', '')
        html_output += f'<div class="kw-source-text">{safe_text}</div>\n'
        
    elif token == "translation":
        html_output += make_heading("translation", "Translation")
        safe_trans = html.escape(ctx.get("sentence_translation", "").replace('\r', ''))
        html_output += f'<div class="kw-translation">{safe_trans}</div>\n'
        
    elif token == "lemmas":
        html_output += make_heading("lemmas", "Lemmas")
        html_output += '<table class="kw-lemmas-table">\n'
        
        COLUMN_TOKEN_MAP = {
            'inflected': 'WordSourceInflectedForm',
            'lemma': 'WordSource',
            'ipa': 'WordSourceIPA',
            'morphology': 'WordSourceMorphologyAI',
            'translation': 'WordDestination'
        }
        
        valid_tokens = []
        html_output += '<thead><tr>'
        for col_token in ctx.get('column_tokens', []):
            if col_token not in COLUMN_TOKEN_MAP:
                logger.warning(f"Unknown lemma_columns token: {col_token}")
                continue
            valid_tokens.append(col_token)
            html_output += f'<th>{col_token.capitalize()}</th>'
        html_output += '</tr></thead>\n<tbody>\n'
        
        headers = ctx['headers']
        data_rows = ctx['data_rows']
        
        col_indices = {}
        for t in valid_tokens:
            field = COLUMN_TOKEN_MAP[t]
            col_indices[t] = headers.index(field) if field in headers else -1
            
        for row in data_rows:
            html_output += '<tr>'
            for t in valid_tokens:
                idx = col_indices[t]
                val = row[idx] if idx != -1 and len(row) > idx else ""
                if isinstance(val, str):
                    val = val.replace('\r', '')
                html_output += f'<td>{val}</td>'
            html_output += '</tr>\n'
            
        html_output += '</tbody></table>\n'
        
    return html_output

def render_lookup_html(text, language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation):
    sections = goldendict['sections']
    column_tokens = goldendict['lemma_columns']
    
    headings = {
        'source': goldendict.get('heading_source', ''),
        'translation': goldendict.get('heading_translation', ''),
        'lemmas': goldendict.get('heading_lemmas', '')
    }
    
    ctx = {
        'text': text,
        'sentence_translation': sentence_translation,
        'headers': headers,
        'data_rows': data_rows,
        'language': language,
        'target_lang': target_lang,
        'run_intellifiller': goldendict['run_intellifiller'],
        'column_tokens': column_tokens,
        'headings': headings
    }
    
    html_output = '<div class="kw-lookup-container">\n'
    for sec in sections:
        html_output += render_section(sec, ctx)
    html_output += '</div>\n'
    
    theme = goldendict.get('theme', 'compact')
    
    css = ""
    if theme == 'compact':
        css = """
        .kw-lookup-container {
            --table-th-border: #ccc;
            --table-border: #eee;
            margin: 0;
            padding: 2px;
            font-family: inherit;
            font-size: inherit;
            line-height: inherit;
            font-style: normal;
            font-weight: normal;
        }
        .kw-source-text {
            white-space: pre-wrap;
            padding: 2px 0;
            margin-bottom: 1em;
            font-family: inherit;
            font-style: normal;
            font-weight: normal;
        }
        .kw-translation {
            padding: 2px 0;
            margin-bottom: 1em;
            font-family: inherit;
            font-style: normal;
            font-weight: normal;
        }
        .kw-lemmas-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 4px;
        }
        .kw-lemmas-table th {
            text-align: left;
            padding: 2px 4px;
            border-bottom: 1px solid var(--table-th-border);
        }
        .kw-lemmas-table td {
            padding: 2px 4px;
        }
        .kw-lemmas-table tr {
            border-bottom: 1px solid var(--table-border);
        }"""
    else:
        css = """
        .kw-lookup-container {
            --bg-color: #0d0f12;
            --text-color: #e3e6eb;
            --section-bg: rgba(255, 255, 255, 0.03);
            --table-th-border: rgba(255, 255, 255, 0.1);
            --table-border: rgba(255, 255, 255, 0.05);
            background-color: var(--bg-color);
            color: var(--text-color);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
            margin: 0;
            padding: 10px;
        }
        .kw-source-text {
            white-space: pre-wrap;
            font-family: monospace;
            padding: 10px;
            background: var(--section-bg);
            border-radius: 5px;
        }
        .kw-translation {
            padding: 10px;
            font-weight: bold;
        }
        .kw-lemmas-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 10px;
        }
        .kw-lemmas-table th {
            text-align: left;
            padding: 8px;
            border-bottom: 1px solid var(--table-th-border);
        }
        .kw-lemmas-table td {
            padding: 8px;
        }
        .kw-lemmas-table tr {
            border-bottom: 1px solid var(--table-border);
        }"""
        if theme == 'light':
            css = css.replace('#0d0f12', '#f6f8fa').replace('#e3e6eb', '#24292f').replace('rgba(255, 255, 255, 0.03)', 'rgba(0, 0, 0, 0.03)').replace('rgba(255, 255, 255, 0.1)', 'rgba(0, 0, 0, 0.1)').replace('rgba(255, 255, 255, 0.05)', 'rgba(0, 0, 0, 0.05)')

    if goldendict.get('disable_css', False):
        base_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Kardenwort Lookup</title>
</head>
<body>
{html_output}
</body>
</html>"""
    else:
        base_html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Kardenwort Lookup</title>
    <style>{css}
    </style>
</head>
<body>
{html_output}
</body>
</html>"""
    return base_html

def render_lookup_text(text, language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation):
    import re
    sections = goldendict['sections']
    column_tokens = goldendict['lemma_columns']
    
    headings = {
        'source': goldendict.get('heading_source', ''),
        'translation': goldendict.get('heading_translation', ''),
        'lemmas': goldendict.get('heading_lemmas', '')
    }
    
    out = []
    
    def add_heading(key, default):
        h = headings.get(key, "")
        if h == '__default__':
            h = default
        if h:
            out.append(f"=== {h} ===")
            
    for sec in sections:
        if sec == 'source':
            add_heading("source", "Source Text")
            out.append(text)
            out.append("")
        elif sec == 'translation':
            add_heading("translation", "Translation")
            out.append(sentence_translation)
            out.append("")
        elif sec == 'lemmas':
            add_heading("lemmas", "Lemmas")
            
            COLUMN_TOKEN_MAP = {
                'inflected': 'WordSourceInflectedForm',
                'lemma': 'WordSource',
                'ipa': 'WordSourceIPA',
                'morphology': 'WordSourceMorphologyAI',
                'translation': 'WordDestination'
            }
            
            valid_tokens = []
            for t in column_tokens:
                if t in COLUMN_TOKEN_MAP:
                    valid_tokens.append(t)
                else:
                    logger.warning(f"Unknown lemma_columns token: {t}")
            
            col_indices = {}
            for t in valid_tokens:
                field = COLUMN_TOKEN_MAP[t]
                col_indices[t] = headers.index(field) if field in headers else -1
                
            for row in data_rows:
                row_vals = []
                for t in valid_tokens:
                    idx = col_indices[t]
                    val = row[idx] if idx != -1 and len(row) > idx else ""
                    val = re.sub(r'<br\s*/?>', ' ', val, flags=re.IGNORECASE)
                    val = re.sub(r'<[^>]+>', '', val)
                    row_vals.append(val.strip())
                out.append("\t".join(row_vals))
            out.append("")
            
    return "\n".join(out).strip()

def render_lookup_combined(text, language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation):
    import json
    html_out = render_lookup_html(text, language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation)
    text_out = render_lookup_text(text, language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation)
    return json.dumps({
        "html": html_out,
        "text": text_out
    }, ensure_ascii=False)

def cmd_lookup(args):
    import datetime, sys, subprocess, configparser
    setup_logging(args.verbose, args.debug)
    zid = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
    logger.info("Lookup subcommand invoked", extra={"zid": zid})
    
    try:
        config, resolved_paths, goldendict = load_config(args.config)
        
        if args.format:
            goldendict['format'] = args.format
        if args.sections:
            goldendict['sections'] = parse_sections_list(args.sections, ['source', 'translation', 'lemmas'])
        if args.lemma_columns:
            goldendict['lemma_columns'] = parse_columns_list(args.lemma_columns, ['inflected', 'lemma', 'ipa', 'morphology', 'translation'])
        if args.no_headings:
            goldendict['heading_source'] = ""
            goldendict['heading_translation'] = ""
            goldendict['heading_lemmas'] = ""
            
        if args.disable_css:
            goldendict['disable_css'] = True
            
        target_lang = args.target_lang if args.target_lang else config.get('settings', 'default_target_language', fallback='ru')
        
        if f"{args.language}_prompt" not in config['languages']:
            raise KeyError(f"Missing {args.language}_prompt in [languages]")
            
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8')
            
        text_mode = getattr(args, 'text_mode', 'single')
        if text_mode == 'single' and '\n' in args.text.strip():
            text_mode = 'multi'
            
        if text_mode == 'multi':
            remove_empty = config.getboolean('settings', 'multi_mode_remove_empty_lines', fallback=True)
            clean_spaces = config.getboolean('settings', 'multi_mode_clean_spaces', fallback=True)
            if remove_empty or clean_spaces:
                import re
                new_lines = []
                for line in args.text.splitlines():
                    if clean_spaces:
                        line = re.sub(r'[ \t]+', ' ', line).strip()
                    if remove_empty and not line.strip():
                        continue
                    new_lines.append(line)
                args.text = "\n".join(new_lines)
            
        comments, headers, data_rows, sentence_translation = run_lookup_flow(
            args.text, args.language, target_lang, goldendict['format'], config, resolved_paths, goldendict, zid, text_mode
        )
        
        fmt = goldendict['format']
        if fmt == 'html':
            out = render_lookup_html(args.text, args.language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation)
        elif fmt == 'text':
            out = render_lookup_text(args.text, args.language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation)
        else:
            out = render_lookup_combined(args.text, args.language, target_lang, config, resolved_paths, zid, goldendict, comments, headers, data_rows, sentence_translation)
            
        print(out)
        sys.exit(0)
    except (configparser.Error, KeyError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        if isinstance(e, subprocess.CalledProcessError):
            print_structured_error("LOOKUP_FAILED", f"Lookup failed with exit code {e.returncode}", {"stderr": getattr(e, 'stderr', str(e))})
        elif isinstance(e, subprocess.TimeoutExpired):
            print_structured_error("LOOKUP_TIMEOUT", f"Lookup timed out after {e.timeout} seconds")
        else:
            print_structured_error("LOOKUP_CONFIG_ERROR", f"Configuration error: {str(e)}")
            
        fmt = getattr(args, 'format', 'html')
        try:
            if 'goldendict' in locals() and 'format' in goldendict:
                fmt = goldendict['format']
        except Exception:
            pass
            
        err_msg = str(e)
        if fmt == 'text':
            print(f"Error: {err_msg}")
        else:
            print(f'<div style="color: red; padding: 10px; font-family: sans-serif;">Error: {err_msg}</div>')
        sys.exit(1)

def cmd_render(args):
    logger.info("Render subcommand invoked", extra={"zid": args.zid})
    config, resolved_paths, goldendict = load_config(args.config)
    
    if not args.text:
        if not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            print_structured_error("INVALID_ARGS", "No text provided to render")
            sys.exit(1)
    else:
        text = args.text
        
    text_mode = getattr(args, 'text_mode', 'single')
    if text_mode == 'single' and '\n' in text.strip():
        text_mode = 'multi'
        
    if text_mode == 'multi':
        remove_empty = config.getboolean('settings', 'multi_mode_remove_empty_lines', fallback=True)
        clean_spaces = config.getboolean('settings', 'multi_mode_clean_spaces', fallback=True)
        if remove_empty or clean_spaces:
            import re
            new_lines = []
            for line in text.splitlines():
                if clean_spaces:
                    line = re.sub(r'[ \t]+', ' ', line).strip()
                if remove_empty and not line.strip():
                    continue
                new_lines.append(line)
            text = "\n".join(new_lines)
        
    try:
        zoom_val = args.zoom if args.zoom else config.get('settings', 'default_zoom', fallback='100')
        split_gap = args.split_gap_limit if args.split_gap_limit is not None else config.getint('settings', 'split_gap_limit', fallback=60)
        html = run_render_flow(text, args.language, args.zid, args.text_mode, config, resolved_paths, zoom_val, args.theme, args.tsv, split_gap_limit=split_gap)
        from b64util import encode
        print(encode(html))
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Render failed: {str(e)}")
        sys.exit(1)

def cmd_export(args):
    logger.info("Export subcommand invoked")
    config, resolved_paths, goldendict = load_config(args.config)
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    kw_config = load_kardenwort_config(kardenwort_workspace)
    
    results_dir_name = kw_config.get('project_structure', 'generated_results_dir', fallback='results')
    results_dir = (kardenwort_workspace / results_dir_name).resolve()
    
    manifest_path = Path(args.selection_manifest).resolve()
    if not manifest_path.exists():
        print_structured_error("INVALID_ARGS", f"Selection manifest not found: {manifest_path}")
        sys.exit(1)
        
    try:
        with open(manifest_path, 'r', encoding='utf-8-sig') as f:
            manifest = json.load(f)
    except Exception as e:
        print_structured_error("INVALID_ARGS", f"Failed to parse selection manifest: {e}")
        sys.exit(1)
        
    selected_rows = manifest.get("selected_row_ids", [])
    zid = manifest.get("zid")
    if not zid:
        print_structured_error("INVALID_ARGS", "Selection manifest must contain 'zid'")
        sys.exit(1)
        
    lang = args.language or config.get('settings', 'default_language', fallback='en')
    
    tsv_path_str = manifest.get("tsv_path")
    if tsv_path_str:
        tsv_path = Path(tsv_path_str)
    else:
        tsv_path = find_working_tsv(results_dir, zid, lang)
        
    if not tsv_path or not tsv_path.exists():
        print_structured_error("DESK_FAILED", f"Working TSV file not found for session ZID {zid}")
        sys.exit(1)
        
    try:
        comments, headers, data_rows = load_tsv_rows(tsv_path)
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Failed to read working TSV: {e}")
        sys.exit(1)
        
    export_selection_mode = config.get('settings', 'export_selection_mode', fallback='selected').lower()
    if export_selection_mode == 'all':
        actual_export_rows = list(range(len(data_rows)))
    elif export_selection_mode == 'unselected':
        actual_export_rows = [i for i in range(len(data_rows)) if i not in selected_rows]
    else:
        actual_export_rows = selected_rows
        
    if not actual_export_rows:
        logger.warning("No rows to export based on selection mode.")
        print("Warning: No rows to export based on selection mode. Export skipped.")
        sys.exit(0)
        
    exported_rows = []
    for row_id in actual_export_rows:
        if 0 <= row_id < len(data_rows):
            exported_rows.append(data_rows[row_id])
        else:
            logger.warning(f"Export row index {row_id} is out of bounds (total rows: {len(data_rows)})")
            
    if not exported_rows:
        print("Warning: None of the selected row indices were valid.")
        sys.exit(0)
        
    fav_dir = resolved_paths['favorites_output_dir']
    fav_dir.mkdir(parents=True, exist_ok=True)
    
    fav_prefix = config.get('settings', 'favorites_prefix', fallback='')
    dest_filename = f"{fav_prefix}{tsv_path.name}"
    dest_path = fav_dir / dest_filename
    
    save_to_favorites = config.getboolean('settings', 'save_to_favorites_on_export', fallback=True)
    import_path = dest_path if save_to_favorites else (results_dir / f"temp_import_{dest_filename}")
    
    try:
        with file_lock(import_path):
            save_tsv_rows_safely(import_path, comments, headers, exported_rows)
        if save_to_favorites:
            logger.info(f"Exported favorites to {import_path}")
            
            copy_txt = config.getboolean('settings', 'copy_source_txt_to_favorites_on_export', fallback=False)
            if copy_txt:
                txt_files = list(tsv_path.parent.glob(f"{zid}-*.txt"))
                for txt_file in txt_files:
                    try:
                        dest_txt_path = fav_dir / f"{fav_prefix}{txt_file.name}"
                        shutil.copy2(txt_file, dest_txt_path)
                        logger.info(f"Copied source text {txt_file.name} to favorites")
                    except Exception as e:
                        logger.error(f"Failed to copy source text {txt_file.name} to favorites: {e}")
        else:
            logger.info(f"Exported temporary file for Anki import to {import_path}")
        
        send_to_anki = config.getboolean('settings', 'send_to_anki_after_export', fallback=False)
        if send_to_anki:
            detach = config.getboolean('settings', 'detach_import_on_send', fallback=True)
            show_window = config.getboolean('settings', 'show_import_window', fallback=False)
            if detach:
                pid, log_path = run_detached_import(import_path, config, resolved_paths, zid)
                response = {
                    "import_started": True,
                    "show_window": show_window,
                    "pid": pid,
                    "log": log_path,
                    "tsv": str(import_path),
                    "note": "safe to close the window"
                }
                print(json.dumps(response))
            else:
                success, output = run_synchronous_import(import_path, config, resolved_paths)
                if success:
                    print(json.dumps({"import_complete": True, "show_window": show_window, "output": output}))
                else:
                    print_structured_error("IMPORT_FAILED", "Anki import failed synchronously", {"details": output})
                    sys.exit(1)
        else:
            if save_to_favorites:
                show_window = config.getboolean('settings', 'show_import_window', fallback=False)
                print(json.dumps({"import_complete": True, "show_window": show_window, "output": f"SUCCESS: Exported to {import_path}"}))
            else:
                print(f"SUCCESS: Ready for Anki (no favorites file created)")
    except Exception as e:
        print_structured_error("EXPORT_FAILED", f"Failed to save exported favorites: {e}")
        sys.exit(1)

def cmd_reprocess(args):
    logger.info("Reprocess subcommand invoked")
    config, resolved_paths, goldendict = load_config(args.config)
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    kw_config = load_kardenwort_config(kardenwort_workspace)
    
    results_dir_name = kw_config.get('project_structure', 'generated_results_dir', fallback='results')
    results_dir = (kardenwort_workspace / results_dir_name).resolve()
    
    manifest_path = Path(args.selection_manifest).resolve()
    if not manifest_path.exists():
        print_structured_error("INVALID_ARGS", f"Selection manifest not found: {manifest_path}")
        sys.exit(1)
        
    try:
        with open(manifest_path, 'r', encoding='utf-8-sig') as f:
            manifest = json.load(f)
    except Exception as e:
        print_structured_error("INVALID_ARGS", f"Failed to parse selection manifest: {e}")
        sys.exit(1)
        
    selected_rows = manifest.get("selected_row_ids", [])
    zid = manifest.get("zid")
    if not zid:
        print_structured_error("INVALID_ARGS", "Selection manifest must contain 'zid'")
        sys.exit(1)
        
    if not selected_rows:
        logger.warning("No rows selected for reprocess.")
        print("Warning: No rows selected. Reprocess skipped.")
        sys.exit(0)
        
    lang = args.language or config.get('settings', 'default_language', fallback='en')
    
    tsv_path_str = manifest.get("tsv_path")
    if tsv_path_str:
        tsv_path = Path(tsv_path_str)
    else:
        tsv_path = find_working_tsv(results_dir, zid, lang)
        
    if not tsv_path or not tsv_path.exists():
        print_structured_error("DESK_FAILED", f"Working TSV file not found for session ZID {zid}")
        sys.exit(1)
        
    try:
        comments, headers, data_rows = load_tsv_rows(tsv_path)
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Failed to read working TSV: {e}")
        sys.exit(1)
        
    mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
    ai_cols = ['WordSourceMorphologyAI', 'WordSourceIPA', 'WordRussian', 'WordEnglish', 'WordUkrainian']
    editable_cols = [c.strip() for c in mapping.get('desk_editable', 'editable_columns', fallback='').split(',') if c.strip()]
    fields_to_clear = [c for c in editable_cols if c not in ('WordSource', 'WordSourceInflectedForm')]
    for col in ai_cols:
        if col not in fields_to_clear:
            fields_to_clear.append(col)
            
    cleared_count = 0
    for row_id in selected_rows:
        if 0 <= row_id < len(data_rows):
            for col in fields_to_clear:
                if col in headers:
                    col_idx = headers.index(col)
                    if len(data_rows[row_id]) > col_idx:
                        data_rows[row_id][col_idx] = ""
            cleared_count += 1
            
    if cleared_count == 0:
        print("Warning: None of the selected row indices were valid.")
        sys.exit(0)
        
    try:
        with file_lock(tsv_path):
            save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
            
        role_fields = get_role_fields(mapping, headers)
        run_enrich = config.get('triggers', 'run_lemma_enrichment', fallback='auto')
        if run_enrich == 'auto':
            write_update_js(tsv_path, data_rows, headers, role_fields)
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Failed to save working TSV after clearing fields: {e}")
        sys.exit(1)
        
    prompt_name = config.get('languages', f'{lang}_prompt')
    logger.info(f"Triggering IntelliFiller async to reprocess {cleared_count} rows in batches.")
    
    # Spawn the batch worker
    python_exe = sys.executable
    desk_script = Path(__file__).resolve()
    
    cmd = [
        str(python_exe),
        str(desk_script),
        "batch-worker",
        "--tsv", str(tsv_path),
        "--prompt", prompt_name,
        "--rows", ",".join(str(r) for r in selected_rows)
    ]
    if args.config:
        cmd.extend(["--config", args.config])
        
    log_path = tsv_path.with_suffix('.log')
    try:
        log_file = open(log_path, 'a', encoding='utf-8')
    except Exception:
        log_file = subprocess.DEVNULL
        
    if sys.platform == 'win32':
        creationflags = 0x08000000 | 0x00000200
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            creationflags=creationflags,
            close_fds=True
        )
    else:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            close_fds=True
        )
    print(json.dumps({"reprocess_started": True, "rows": cleared_count}))

def _reprocess_worker_stage_fast_path(tsv_path, config, resolved_paths, data_rows, headers, role_fields, selected_rows, lemmas_provider, language, target_lang):
    col_lemma_name = role_fields.get('lemma', 'WordSource')
    col_word_dest_name = role_fields.get('word_translation', 'WordRussian')
    
    col_lemma = headers.index(col_lemma_name) if col_lemma_name in headers else -1
    col_word_dest = headers.index(col_word_dest_name) if col_word_dest_name in headers else -1
    
    if col_lemma != -1 and col_word_dest != -1:
        lemmas_to_translate = []
        for row_id in selected_rows:
            if 0 <= row_id < len(data_rows):
                row = data_rows[row_id]
                if len(row) > col_lemma and row[col_lemma].strip():
                    lemmas_to_translate.append(row[col_lemma].strip())
        
        if lemmas_to_translate:
            lemmas_to_translate = list(set(lemmas_to_translate))
            provider_to_use = 'combined' if lemmas_provider == 'combined' else lemmas_provider
            lemma_translations = translate_lemmas_fast_path(lemmas_to_translate, language, target_lang, config, resolved_paths, provider_to_use)
            
            with file_lock(tsv_path):
                comments, headers, data_rows = load_tsv_rows(tsv_path)
                for row_id in selected_rows:
                    if 0 <= row_id < len(data_rows):
                        row = data_rows[row_id]
                        if len(row) > col_lemma:
                            lemma_val = row[col_lemma].strip()
                            while len(row) <= col_word_dest:
                                row.append("")
                            if lemma_val in lemma_translations:
                                row[col_word_dest] = lemma_translations[lemma_val]
                save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
                
            run_enrich = config.get('triggers', 'run_lemma_enrichment', fallback='auto')
            if run_enrich == 'auto':
                write_update_js(tsv_path, data_rows, headers, role_fields)
    return data_rows

def _reprocess_worker_stage_intellifiller(tsv_path, args, config, resolved_paths, data_rows, headers, role_fields, selected_rows):
    batch_size = config.getint('settings', 'intellifiller_batch_size', fallback=5)
    for i in range(0, len(selected_rows), batch_size):
        batch = selected_rows[i:i + batch_size]
        logger.info(f"Running IntelliFiller for batch {i // batch_size + 1}: {len(batch)} rows.")
        run_headless_intellifiller(tsv_path, args.prompt, config, resolved_paths, selected_rows=batch)
        
        try:
            with file_lock(tsv_path):
                comments, headers, data_rows = load_tsv_rows(tsv_path)
            run_enrich = config.get('triggers', 'run_lemma_enrichment', fallback='auto')
            if run_enrich == 'auto':
                write_update_js(tsv_path, data_rows, headers, role_fields)
        except Exception as e:
            logger.error(f"Failed to write update JS after IntelliFiller batch: {e}")
    return data_rows

def cmd_reprocess_worker(args):
    config, resolved_paths, goldendict = load_config(args.config)
    tsv_path = Path(args.tsv)
    
    rows_str = args.rows
    if not rows_str:
        return
        
    selected_rows = [int(r.strip()) for r in rows_str.split(',') if r.strip()]
    lemmas_provider = config.get('pipeline', 'lemma_reprocess_provider', fallback='intellifiller')
    language = config.get('settings', 'default_language', fallback='en')
    target_lang = config.get('settings', 'default_target_language', fallback='ru')
    
    data_rows, headers, role_fields = [], [], {}
    
    try:
        with file_lock(tsv_path):
            comments, headers, data_rows = load_tsv_rows(tsv_path)
            
        mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
        role_fields = get_role_fields(mapping, headers)
        
        if lemmas_provider in ('combined', 'google', 'deepl'):
            try:
                data_rows = _reprocess_worker_stage_fast_path(tsv_path, config, resolved_paths, data_rows, headers, role_fields, selected_rows, lemmas_provider, language, target_lang)
                write_update_js(tsv_path, data_rows, headers, role_fields)
            except Exception as e:
                logger.error(f"Failed fast-path translation during reprocess: {e}")

        if lemmas_provider in ('intellifiller', 'combined'):
            try:
                data_rows = _reprocess_worker_stage_intellifiller(tsv_path, args, config, resolved_paths, data_rows, headers, role_fields, selected_rows)
            except Exception as e:
                logger.error(f"Failed IntelliFiller stage during reprocess: {e}")
                
    except Exception as e:
        logger.error(f"Unhandled exception in cmd_reprocess_worker: {e}")
    finally:
        try:
            write_update_js(tsv_path, data_rows, headers, role_fields, stage="finished")
        except Exception as e:
            logger.error(f"Failed to write finished event in reprocess: {e}")

def write_update_js(tsv_path, data_rows, headers, role_fields, stage=None, status="success", source_text=None, translated_text=None):
    import time
    update_js_path = tsv_path.with_suffix('.update.js')
    
    col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else -1
    col_inflected = headers.index(role_fields['inflected']) if 'inflected' in role_fields and role_fields['inflected'] in headers else -1
    col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else -1
    col_morph = headers.index(role_fields['morphology']) if 'morphology' in role_fields and role_fields['morphology'] in headers else -1
    col_ipa = headers.index(role_fields['ipa']) if 'ipa' in role_fields and role_fields['ipa'] in headers else -1
    
    rows_data = {}
    for row_id, row in enumerate(data_rows):
        trans_val = row[col_word_dest] if col_word_dest != -1 and len(row) > col_word_dest else ""
        morph_val = row[col_morph] if col_morph != -1 and len(row) > col_morph else ""
        ipa_val = row[col_ipa] if col_ipa != -1 and len(row) > col_ipa else ""
        rows_data[row_id] = {
            "trans": trans_val,
            "ipa": ipa_val,
            "morph": morph_val
        }
        
    if stage is None:
        # Inline snapshot — only emit rows that have at least one non-empty field
        update_data = {
            row_id: d for row_id, d in rows_data.items()
            if d["trans"] or d["ipa"] or d["morph"]
        }
    else:
        if source_text is None:
            source_txt_path = tsv_path.with_suffix('.txt')
            if source_txt_path.exists():
                try:
                    source_text = source_txt_path.read_text(encoding='utf-8')
                except Exception:
                    pass
                    
        if translated_text is None:
            if tsv_path:
                try:
                    parent = tsv_path.parent
                    source_stem_full = tsv_path.stem
                    source_stem = source_stem_full.rsplit('.', 1)[0] if '.' in source_stem_full else source_stem_full
                    for f in parent.glob(f"{source_stem}.*.txt"):
                        if f.stem != source_stem_full:
                            txt_content = f.read_text(encoding='utf-8').strip()
                            if txt_content:
                                lines = [html.escape(line.strip()) for line in txt_content.splitlines()]
                                is_single = True
                                source_txt_path = tsv_path.with_suffix('.txt')
                                if source_txt_path.exists():
                                    try:
                                        src_txt = source_txt_path.read_text(encoding='utf-8').strip()
                                        if '\n' in src_txt or '\r' in src_txt:
                                            is_single = False
                                    except Exception:
                                        pass
                                if is_single:
                                    translated_text = f"<div>{' '.join(lines)}</div>"
                                else:
                                    translated_text = "".join(f"<div>{line if line else '&nbsp;'}</div>" for line in lines)
                                break
                except Exception as e:
                    logger.error(f"Failed to read clean translation text file in write_update_js: {e}")

        if translated_text is None:
            col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
            col_index = headers.index('SentenceSourceIndex') if 'SentenceSourceIndex' in headers else -1
            if col_sentence_dest != -1:
                idx_to_sentence = {}
                for row in data_rows:
                    if len(row) > col_sentence_dest:
                        s = row[col_sentence_dest].strip()
                        if s:
                            idx_val = 0
                            if col_index != -1 and len(row) > col_index and row[col_index].strip():
                                try:
                                    idx_val = int(row[col_index])
                                except ValueError:
                                    pass
                            if idx_val not in idx_to_sentence:
                                idx_to_sentence[idx_val] = s
                
                sorted_keys = sorted(idx_to_sentence.keys())
                sentences = [idx_to_sentence[k] for k in sorted_keys]
                
                is_single = True
                if source_text:
                    stripped_src = source_text.strip()
                    if '\n' in stripped_src or '\r' in stripped_src:
                        is_single = False
                else:
                    source_txt_path = tsv_path.with_suffix('.txt')
                    if source_txt_path.exists():
                        try:
                            txt = source_txt_path.read_text(encoding='utf-8')
                            stripped_txt = txt.strip()
                            if '\n' in stripped_txt or '\r' in stripped_txt:
                                is_single = False
                        except Exception:
                            pass

                if is_single:
                    non_empty = [s for s in sentences if s]
                    if non_empty and all(s == non_empty[0] for s in non_empty):
                        sentences = [non_empty[0]]
                    translated_text = f"<div>{html.escape(' '.join(sentences))}</div>"
                else:
                    translated_text = "".join(f"<div>{html.escape(s)}</div>" for s in sentences)
                
        update_data = {
            "stage": stage,
            "status": status,
            "sourceText": source_text or "",
            "translatedText": translated_text or ""
        }
        if stage != "source":
            update_data["rows"] = rows_data
        
    js_content = f"if (typeof window.receiveUpdate === 'function') {{ window.receiveUpdate({json.dumps(update_data)}); }}"
    
    temp_path = update_js_path.with_name(update_js_path.name + '.tmp')
    with file_lock(update_js_path):
        with open(temp_path, 'w', encoding='utf-8') as f:
            f.write(js_content)
        for attempt in range(10):
            try:
                os.replace(temp_path, update_js_path)
                break
            except PermissionError:
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"Failed to replace update.js (attempt {attempt + 1}): {e}")
                time.sleep(0.1)
        else:
            logger.error(f"Failed to atomically replace update.js after 10 retries: {update_js_path}")

def _progressive_worker_stage_translation(tsv_path, args, config, resolved_paths, data_rows, headers, role_fields):
    col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields and role_fields['lemma'] in headers else -1
    col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields and role_fields['word_translation'] in headers else -1
    col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
    
    run_text = config.get('triggers', 'run_text_translation', fallback='auto')
    run_base = config.get('triggers', 'run_lemma_base_translation', fallback='auto')
    
    try:
        # check if sentence needs translation
        sentence_translated = False
        if col_sentence_dest != -1:
            if any(len(row) > col_sentence_dest and row[col_sentence_dest].strip() for row in data_rows):
                sentence_translated = True
                
        if not sentence_translated and run_text == 'auto':
            source_txt_path = tsv_path.with_suffix('.txt')
            if source_txt_path.exists():
                text = source_txt_path.read_text(encoding='utf-8')
                main_text_provider = config.get('pipeline', 'text_base_provider', fallback=config.get('pipeline', 'lemma_base_provider', fallback='google'))
                col_index = headers.index('SentenceSourceIndex') if 'SentenceSourceIndex' in headers else -1
                try:
                    def on_chunk_done(partial_translations, _text=text, _col_index=col_index, _col_sentence_dest=col_sentence_dest):
                        c, h, curr_rows = load_tsv_rows(tsv_path)
                        resolve_translations(
                            _text, args.text_mode, curr_rows, _col_index, _col_sentence_dest,
                            partial_translations, tsv_path, c, h,
                            persist=True, return_single=False
                        )
                        # Use stage=None to push only table row data.
                        # The TRANSLATE section (paragraph) is updated once at the end
                        # after the full .ru.txt file is written, avoiding blink/flicker.
                        write_update_js(tsv_path, curr_rows, h, role_fields, stage=None)

                    sentence_translations_raw = translate_source_text(
                        text, args.language, args.target_lang, args.text_mode, config, resolved_paths, main_text_provider, chunk_callback=on_chunk_done)
                    comments, headers, current_rows = load_tsv_rows(tsv_path)
                    resolve_translations(
                        text, args.text_mode, current_rows, col_index, col_sentence_dest,
                        sentence_translations_raw, tsv_path, comments, headers,
                        persist=True, return_single=False
                    )
                    save_translation_text = config.getboolean('settings', 'save_translation_text', fallback=False)
                    slug = generate_slug(text)
                    m = re.match(r"^(\d{14})", tsv_path.name)
                    zid = m.group(1) if m else "session"
                    translation_text_path = tsv_path.parent / f"{zid}-{slug}.{args.target_lang}.txt"
                    eff_mode = _effective_text_mode(text, args.text_mode)
                    _write_translation_txt(text, eff_mode, sentence_translations_raw, translation_text_path, save_flag=save_translation_text, overwrite=True)
                    
                    data_rows = current_rows
                    write_update_js(tsv_path, data_rows, headers, role_fields, stage="translated_text")
                except TranslationAlignmentError as tae:
                    logger.error(f"Progressive translation alignment error: {tae}")
                    comments, headers, current_rows = load_tsv_rows(tsv_path)
                    resolve_translations(
                        text, args.text_mode, current_rows, col_index, col_sentence_dest,
                        tae.partial_dict, tsv_path, comments, headers,
                        persist=True, return_single=False
                    )
                    save_translation_text = config.getboolean('settings', 'save_translation_text', fallback=False)
                    slug = generate_slug(text)
                    m = re.match(r"^(\d{14})", tsv_path.name)
                    zid = m.group(1) if m else "session"
                    translation_text_path = tsv_path.parent / f"{zid}-{slug}.{args.target_lang}.txt"
                    eff_mode = _effective_text_mode(text, args.text_mode)
                    _write_translation_txt(text, eff_mode, tae.partial_dict, translation_text_path, save_flag=save_translation_text, overwrite=True)
                    
                    data_rows = current_rows
                    write_update_js(tsv_path, data_rows, headers, role_fields, stage="translated_text", status="partial_persisted")
                    
                    sys.exit(EXIT_PARTIAL_TRANSLATION_PERSISTED)
        
        # check if lemmas need translation
        word_translations_empty = args.word_empty.lower() == 'true'
        if word_translations_empty and col_lemma != -1 and run_base == 'auto':
            # Preserve row order while deduplicating (set() destroys order via hash)
            seen = set()
            lemmas_to_translate = []
            for row in data_rows:
                if col_lemma != -1 and len(row) > col_lemma and row[col_lemma].strip():
                    val = row[col_lemma]
                    if val not in seen:
                        seen.add(val)
                        lemmas_to_translate.append(val)
            
            translation_order = config.get('translation', 'translation_order', fallback='top_to_bottom').strip().lower()
            if translation_order == 'bottom_to_top':
                lemmas_to_translate = list(reversed(lemmas_to_translate))
            if lemmas_to_translate:
                provider = config.get('pipeline', 'lemma_base_provider', fallback='google')
                chunk_size = config.getint('translation', 'translation_chunk_size', fallback=0)
                if chunk_size > 0:
                    chunks = [lemmas_to_translate[i:i + chunk_size] for i in range(0, len(lemmas_to_translate), chunk_size)]
                else:
                    chunks = [lemmas_to_translate]
                    
                for chunk in chunks:
                    lemma_translations = translate_lemmas_fast_path(chunk, args.language, args.target_lang, config, resolved_paths, provider)
                    
                    with file_lock(tsv_path):
                        comments, headers, current_rows = load_tsv_rows(tsv_path)
                        for row in current_rows:
                            if col_lemma != -1 and len(row) > col_lemma:
                                lemma_val = row[col_lemma]
                                if col_word_dest != -1:
                                    while len(row) <= col_word_dest:
                                        row.append("")
                                    if not row[col_word_dest].strip():
                                        if lemma_val in lemma_translations:
                                            row[col_word_dest] = lemma_translations[lemma_val]
                        save_tsv_rows_safely(tsv_path, comments, headers, current_rows)
                        data_rows = current_rows
                        
                    write_update_js(tsv_path, data_rows, headers, role_fields, stage="translated")
            else:
                write_update_js(tsv_path, data_rows, headers, role_fields, stage="translated")
        else:
            write_update_js(tsv_path, data_rows, headers, role_fields, stage="translated")
    except Exception as e:
        logger.error(f"Failing in translated stage: {e}")
        write_update_js(tsv_path, data_rows, headers, role_fields, stage="translated", status="failed")
    return data_rows

def _progressive_worker_stage_enrichment(tsv_path, args, config, resolved_paths, data_rows, headers, role_fields):
    try:
        batch_size = config.getint('settings', 'intellifiller_batch_size', fallback=5)
        selected_rows = list(range(len(data_rows)))
        for i in range(0, len(selected_rows), batch_size):
            batch = selected_rows[i:i + batch_size]
            logger.info(f"Running IntelliFiller for progressive batch {i // batch_size + 1}: {len(batch)} rows.")
            run_headless_intellifiller(tsv_path, args.prompt, config, resolved_paths, selected_rows=batch)
            
            # reload data rows after each batch
            comments, headers, data_rows = load_tsv_rows(tsv_path)
            write_update_js(tsv_path, data_rows, headers, role_fields, stage="enrichment")
    except Exception as e:
        logger.error(f"Failing in enrichment stage: {e}")
        write_update_js(tsv_path, data_rows, headers, role_fields, stage="enrichment", status="failed")
    return data_rows

def cmd_retext(args):
    logger.info("Retext subcommand invoked")
    config, resolved_paths, goldendict = load_config(args.config)
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    kw_config = load_kardenwort_config(kardenwort_workspace)
    
    results_dir_name = kw_config.get('project_structure', 'generated_results_dir', fallback='results')
    results_dir = (kardenwort_workspace / results_dir_name).resolve()
    
    manifest_path = Path(args.selection_manifest).resolve()
    if not manifest_path.exists():
        print_structured_error("INVALID_ARGS", f"Selection manifest not found: {manifest_path}")
        sys.exit(1)
        
    try:
        with open(manifest_path, 'r', encoding='utf-8-sig') as f:
            manifest = json.load(f)
    except Exception as e:
        print_structured_error("INVALID_ARGS", f"Failed to parse selection manifest: {e}")
        sys.exit(1)
        
    zid = manifest.get("zid")
    if not zid:
        print_structured_error("INVALID_ARGS", "Selection manifest must contain 'zid'")
        sys.exit(1)
        
    lang = args.language or config.get('settings', 'default_language', fallback='en')
    
    tsv_path_str = manifest.get("tsv_path")
    if tsv_path_str:
        tsv_path = Path(tsv_path_str)
    else:
        tsv_path = find_working_tsv(results_dir, zid, lang)
        
    if not tsv_path or not tsv_path.exists():
        print_structured_error("DESK_FAILED", f"Working TSV file not found for session ZID {zid}")
        sys.exit(1)
        
    logger.info("Triggering async retext worker.")
    
    python_exe = sys.executable
    desk_script = Path(__file__).resolve()
    
    cmd = [
        str(python_exe),
        str(desk_script),
        "retext-worker",
        "--tsv", str(tsv_path),
        "--language", lang,
        "--text-mode", args.text_mode
    ]
    if args.config:
        cmd.extend(["--config", args.config])
        
    log_path = tsv_path.with_suffix('.log')
    try:
        log_file = open(log_path, 'a', encoding='utf-8')
    except Exception:
        log_file = subprocess.DEVNULL
        
    if sys.platform == 'win32':
        creationflags = 0x08000000 | 0x00000200
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            creationflags=creationflags,
            close_fds=True
        )
    else:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            close_fds=True
        )
    print(json.dumps({"retext_started": True}))

def cmd_retext_worker(args):
    config, resolved_paths, goldendict = load_config(args.config)
    tsv_path = Path(args.tsv)
    language = args.language
    text_mode = args.text_mode
    target_lang = config.get('settings', 'default_target_language', fallback='ru')
    
    try:
        with file_lock(tsv_path):
            comments, headers, data_rows = load_tsv_rows(tsv_path)
            
        mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
        role_fields = get_role_fields(mapping, headers)
        
        source_text_path = tsv_path.with_suffix('.txt')
        if not source_text_path.exists():
            logger.error("Source text file missing for retext")
            return
            
        text = source_text_path.read_text(encoding='utf-8')
        text_reprocess_provider = config.get('pipeline', 'text_reprocess_provider', fallback='deepl')
        logger.info(f"Retext worker translating using provider {text_reprocess_provider}")
        
        try:
            sentence_translations = translate_source_text(text, language, target_lang, text_mode, config, resolved_paths, text_reprocess_provider)
        except TranslationAlignmentError as tae:
            logger.error(f"Retext worker translation alignment error: {tae}")
            sentence_translations = tae.partial_dict
            
        slug = generate_slug(text)
        m = re.match(r"^(\d{14})", tsv_path.name)
        zid = m.group(1) if m else "session"
        target_text_path = tsv_path.parent / f"{zid}-{slug}.{target_lang}.txt"
        eff_mode = _effective_text_mode(text, text_mode)
        _write_translation_txt(text, eff_mode, sentence_translations, target_text_path, save_flag=True, overwrite=True)
        
        comments, headers, data_rows = load_tsv_rows(tsv_path)
        col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields and role_fields['sentence_destination'] in headers else -1
        col_index = headers.index('SentenceSourceIndex') if 'SentenceSourceIndex' in headers else -1
        
        resolve_translations(
            text, text_mode, data_rows, col_index, col_sentence_dest,
            sentence_translations, tsv_path, comments, headers,
            persist=True, return_single=False
        )
        comments, headers, data_rows = load_tsv_rows(tsv_path)
        # source_text="" because retext never changes the source text;
        # sending it would cause receiveUpdate to wipe the span DOM.
        write_update_js(tsv_path, data_rows, headers, role_fields, stage="finished", source_text="")
    except Exception as e:
        logger.error(f"Unhandled exception in cmd_retext_worker: {e}")
        try:
            with file_lock(tsv_path):
                comments, headers, data_rows = load_tsv_rows(tsv_path)
            mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
            role_fields = get_role_fields(mapping, headers)
            write_update_js(tsv_path, data_rows, headers, role_fields, stage="finished", source_text="")
        except Exception:
            pass
def cmd_progressive_worker(args):
    tsv_path = Path(args.tsv)
    log_path = tsv_path.with_suffix('.log')
    file_handler = None
    try:
        try:
            file_handler = logging.FileHandler(log_path, mode='a', encoding='utf-8')
            file_handler.setFormatter(JSONFormatter())
            logger.addHandler(file_handler)
        except Exception as log_err:
            sys.stderr.write(f"Warning: Failed to setup progressive-worker log file: {log_err}\n")
            
        logger.info("Progressive-worker subcommand invoked")
        config, resolved_paths, goldendict = load_config(args.config)
        import os
        os.environ["KARDEN_ACTIVE_TEXT_MODE"] = args.text_mode
        
        if not tsv_path.exists():
            return
            
        comments, headers, data_rows = [], [], []
        role_fields = {}
        
        try:
            comments, headers, data_rows = load_tsv_rows(tsv_path)
            mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
            role_fields = get_role_fields(mapping, headers)
            
            run_base = config.get('triggers', 'run_lemma_base_translation', fallback='auto')
            run_text = config.get('triggers', 'run_text_translation', fallback='auto')
            run_enrich = config.get('triggers', 'run_lemma_enrichment', fallback='auto')
            enrich_provider = config.get('pipeline', 'lemma_reprocess_provider', fallback='intellifiller')
            
            # Write initial source stage
            write_update_js(tsv_path, data_rows, headers, role_fields, stage="source")
            
            # 1. Base Translation Stage
            if run_base == 'auto' or run_text == 'auto':
                data_rows = _progressive_worker_stage_translation(tsv_path, args, config, resolved_paths, data_rows, headers, role_fields)
                    
            # 2. Enrichment Stage
            skip_intellifiller = getattr(args, 'skip_intellifiller', False) or run_enrich == 'manual' or enrich_provider == 'none'
            if not skip_intellifiller:
                data_rows = _progressive_worker_stage_enrichment(tsv_path, args, config, resolved_paths, data_rows, headers, role_fields)
                    
        except SystemExit as se:
            raise se
        except Exception as e:
            logger.error(f"Unhandled exception in cmd_progressive_worker: {e}")
        finally:
            # 3. Finished Event
            try:
                write_update_js(tsv_path, data_rows, headers, role_fields, stage="finished")
            except Exception as e:
                logger.error(f"Failed to write finished event: {e}")
    finally:
        if file_handler:
            logger.removeHandler(file_handler)
            file_handler.close()




def cmd_edit_save(args):
    logger.info("Edit-save subcommand invoked", extra={"zid": args.zid})
    config, resolved_paths, goldendict = load_config(args.config)
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    kw_config = load_kardenwort_config(kardenwort_workspace)
    
    mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
    editable_cols = [c.strip() for c in mapping.get('desk_editable', 'editable_columns', fallback='').split(',') if c.strip()]
    
    deltas_path = Path(args.deltas).resolve()
    if not deltas_path.exists():
        print_structured_error("INVALID_ARGS", f"Deltas file not found: {deltas_path}")
        sys.exit(1)
        
    try:
        with open(deltas_path, 'r', encoding='utf-8-sig') as f:
            deltas = json.load(f)
    except Exception as e:
        print_structured_error("INVALID_ARGS", f"Failed to parse deltas: {e}")
        sys.exit(1)
        
    results_dir_name = kw_config.get('project_structure', 'generated_results_dir', fallback='results')
    results_dir = (kardenwort_workspace / results_dir_name).resolve()
    
    lang = args.language or config.get('settings', 'default_language', fallback='en')
    
    if hasattr(args, 'tsv') and args.tsv:
        tsv_path = Path(args.tsv)
    else:
        tsv_path = find_working_tsv(results_dir, args.zid, lang)
        
    if not tsv_path or not tsv_path.exists():
        print_structured_error("DESK_FAILED", f"Working TSV file not found for session ZID {args.zid}")
        sys.exit(1)
        
    try:
        with file_lock(tsv_path):
            try:
                comments, headers, data_rows = load_tsv_rows(tsv_path)
            except Exception as e:
                print_structured_error("DESK_FAILED", f"Failed to load working TSV: {e}")
                sys.exit(1)
                
            role_fields = {role: field for field, role in mapping['desk_columns'].items() if field in headers}
            selected_col_name = role_fields.get('selected', 'DeskSelected')
            if selected_col_name not in editable_cols:
                editable_cols.append(selected_col_name)
                
            for delta in deltas:
                row_id = delta.get("row_id")
                col_name = delta.get("column")
                val = delta.get("value")
                
                if row_id is None or col_name is None or val is None:
                    print_structured_error("INVALID_ARGS", "Each delta must have 'row_id', 'column', and 'value'")
                    sys.exit(1)
                    
                if col_name == "_delete":
                    if 0 <= row_id < len(data_rows):
                        data_rows[row_id] = None
                    continue
                    
                if col_name not in editable_cols:
                    print_structured_error("DESK_FAILED", f"Column '{col_name}' is not inline-editable.")
                    sys.exit(1)
                    
                if col_name not in headers:
                    print_structured_error("DESK_FAILED", f"Column '{col_name}' not found in TSV headers.")
                    sys.exit(1)
                    
                col_idx = headers.index(col_name)
                if 0 <= row_id < len(data_rows):
                    if data_rows[row_id] is not None:
                        data_rows[row_id][col_idx] = val
                else:
                    print_structured_error("DESK_FAILED", f"Row index {row_id} is out of bounds (total rows: {len(data_rows)})")
                    sys.exit(1)
                    
            data_rows = [r for r in data_rows if r is not None]
            
            save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
        print("SUCCESS")
    except SystemExit:
        raise
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Failed to process and save working TSV: {e}")
        sys.exit(1)

def cmd_merge(args):
    logger.info("Merge subcommand invoked")
    config, resolved_paths, goldendict = load_config(args.config)
    
    files = [Path(f).resolve() for f in args.files]
    files.sort(key=extract_zid)
    
    first_headers = None
    for f in files:
        if not f.exists():
            print_structured_error("INVALID_ARGS", f"File not found: {f}")
            sys.exit(1)
        try:
            _, headers, _ = load_tsv_rows(f)
            if first_headers is None:
                first_headers = headers
            else:
                if headers != first_headers:
                    print_structured_error(
                        "MERGE_SCHEMA_MISMATCH",
                        f"Schema mismatch in file: {f.name}. All files must share the same header."
                    )
                    sys.exit(1)
        except Exception as e:
            print_structured_error("MERGE_FAILED", f"Failed to read file {f.name}: {e}")
            sys.exit(1)
            
    all_comments = []
    all_data_rows = []
    sibling_texts = []
    
    for f in files:
        comments, _, rows = load_tsv_rows(f)
        if not all_comments:
            all_comments = comments
        all_data_rows.extend(rows)
        
        zid = extract_zid(f)
        parent_dir = f.parent
        txt_files = list(parent_dir.glob(f"{zid}-*.txt"))
        if not txt_files:
            base_txt = f.with_suffix('.txt')
            if base_txt.exists():
                txt_files = [base_txt]
        if txt_files:
            try:
                content = txt_files[0].read_text(encoding='utf-8')
                sibling_texts.append(content)
            except Exception as e:
                logger.warning(f"Failed to read sibling text {txt_files[0]}: {e}")
        else:
            logger.warning(f"No sibling .txt found for {f.name}")
            
    dest_dir = files[0].parent
    
    if args.target == "new":
        timestamp_id = datetime.now().strftime('%Y%m%d%H%M%S')
        lang = "en"
        lang_match = re.search(r'\.([a-z]{2})\.tsv$', files[0].name)
        if lang_match:
            lang = lang_match.group(1)
        dest_tsv_path = dest_dir / f"{timestamp_id}-merged.{lang}.tsv"
        dest_txt_path = dest_dir / f"{timestamp_id}-merged.txt"
    elif args.target == "first":
        dest_tsv_path = files[0]
        zid = extract_zid(files[0])
        txt_files = list(files[0].parent.glob(f"{zid}-*.txt"))
        if txt_files:
            dest_txt_path = txt_files[0]
        else:
            dest_txt_path = files[0].with_suffix('.txt')
    else:
        dest_tsv_path = Path(args.target).resolve()
        zid = extract_zid(dest_tsv_path)
        txt_files = list(dest_tsv_path.parent.glob(f"{zid}-*.txt"))
        if txt_files:
            dest_txt_path = txt_files[0]
        else:
            dest_txt_path = dest_tsv_path.with_suffix('.txt')
            
    merged_text = "\n\n".join(sibling_texts)
    
    try:
        with file_lock(dest_tsv_path):
            save_tsv_rows_safely(dest_tsv_path, all_comments, first_headers, all_data_rows)
            
        if dest_txt_path:
            with file_lock(dest_txt_path):
                temp_txt = dest_txt_path.with_suffix('.txt.tmp')
                bak_txt = dest_txt_path.with_suffix('.txt.bak')
                try:
                    temp_txt.write_text(merged_text, encoding='utf-8')
                    if dest_txt_path.exists():
                        if bak_txt.exists():
                            os.remove(bak_txt)
                        os.rename(dest_txt_path, bak_txt)
                    try:
                        os.rename(temp_txt, dest_txt_path)
                    except Exception as e:
                        if bak_txt.exists():
                            os.rename(bak_txt, dest_txt_path)
                        raise e
                    if bak_txt.exists():
                        try:
                            os.remove(bak_txt)
                        except OSError:
                            pass
                except Exception as e:
                    if temp_txt.exists():
                        try:
                            os.remove(temp_txt)
                        except OSError:
                            pass
                    raise e
                    
        delete_sources = config.getboolean('settings', 'merge_delete_sources', fallback=False)
        if delete_sources:
            for f in files:
                if f == dest_tsv_path:
                    continue
                try:
                    os.remove(f)
                    zid = extract_zid(f)
                    for t_file in f.parent.glob(f"{zid}-*.txt"):
                        if t_file != dest_txt_path:
                            os.remove(t_file)
                except Exception as e:
                    logger.warning(f"Failed to delete merged source {f.name}: {e}")
                    
        print(f"SUCCESS: Merged TSV: {dest_tsv_path}, Merged TXT: {dest_txt_path}")
    except Exception as e:
        print_structured_error("MERGE_FAILED", f"Merge execution failed: {e}")
        sys.exit(1)

def spawn_ahk(args_list, base_dir):
    ahk_script = base_dir.parent / "20240411110510-autohotkey" / "kardenwort-window" / "kardenwort-window.ahk"
    
    import shutil
    ahk_exes = ["AutoHotkey.exe", "AutoHotkey64.exe", "AutoHotkey32.exe"]
    found_exe = None
    
    # 1. Try to find any in PATH
    for name in ahk_exes:
        path_match = shutil.which(name)
        if path_match:
            found_exe = path_match
            break
            
    # 2. Check common installation directories
    if not found_exe:
        possible_dirs = [
            Path(r"C:\Program Files\AutoHotkey\v2"),
            Path(r"C:\Program Files\AutoHotkey"),
            Path(r"C:\Program Files (x86)\AutoHotkey"),
        ]
        # Scan C:\AHK and its subfolders (like C:\AHK\AutoHotkey_2.0.18)
        try:
            c_ahk = Path(r"C:\AHK")
            if c_ahk.exists():
                possible_dirs.append(c_ahk)
                for sub in c_ahk.iterdir():
                    if sub.is_dir() and "autohotkey" in sub.name.lower():
                        possible_dirs.append(sub)
        except Exception:
            pass
            
        for p_dir in possible_dirs:
            for name in ahk_exes:
                candidate = p_dir / name
                if candidate.exists():
                    found_exe = str(candidate)
                    break
            if found_exe:
                break
                
    if found_exe:
        cmd = [found_exe, str(ahk_script)] + args_list
        logger.info(f"Spawning AHK via executable: {' '.join(cmd)}")
        try:
            subprocess.Popen(cmd)
        except Exception as e:
            logger.error(f"Failed to spawn AHK window process: {e}")
    else:
        logger.warning(f"No AutoHotkey executable found, falling back to shell execution for {ahk_script.name}")
        try:
            args_str = ' '.join(f'"{a}"' for a in args_list)
            if sys.version_info >= (3, 10):
                os.startfile(str(ahk_script), operation='open', arguments=args_str)
            else:
                subprocess.Popen(["cmd.exe", "/c", "start", '""', str(ahk_script)] + args_list)
        except Exception as e2:
            logger.error(f"Failed to spawn AHK via fallback: {e2}")

def cmd_restore(args):
    logger.info("Restore subcommand invoked")
    config, resolved_paths, goldendict = load_config(args.config)
    
    file_list = args.file if isinstance(args.file, list) else [args.file]
    
    if not args.no_gui:
        ahk_args = []
        zid_groups = {}
        non_zid_files = []
        for file_val in file_list:
            input_path = Path(file_val).resolve()
            if input_path.exists():
                match = re.match(r"^(\d{14})", input_path.name)
                if match:
                    zid = match.group(1)
                    if zid not in zid_groups:
                        zid_groups[zid] = []
                    zid_groups[zid].append(input_path)
                else:
                    non_zid_files.append(input_path)
            else:
                print_structured_error("INVALID_ARGS", f"File to restore not found: {input_path}")
                
        def priority(p):
            ext = p.suffix.lower()
            if ext == '.tsv': return 0
            if ext == '.txt': return 1
            return 2

        for zid, files in zid_groups.items():
            best_file = sorted(files, key=priority)[0]
            ahk_args.extend(["--restore", str(best_file)])
            
        for file_path in non_zid_files:
            ahk_args.extend(["--restore", str(file_path)])

        if ahk_args:
            spawn_ahk(ahk_args, resolved_paths['base_dir'])
        return

    input_path = Path(file_list[0]).resolve()
    if not input_path.exists():
        print_structured_error("INVALID_ARGS", f"File to restore not found: {input_path}")
        sys.exit(1)
        
    zid = extract_zid(input_path)
    parent_dir = input_path.parent
    
    tsv_path = None
    txt_path = None
    warnings = []
    
    if input_path.suffix == '.tsv':
        tsv_path = input_path
        txt_files = list(parent_dir.glob(f"{zid}-*.txt"))
        if txt_files:
            source_lang = None
            if len(input_path.suffixes) >= 2:
                source_lang = input_path.suffixes[-2].strip('.')
            
            def txt_priority(p):
                sufs = p.suffixes
                lang_code = sufs[-2].strip('.') if len(sufs) >= 2 else None
                if source_lang and lang_code == source_lang:
                    return 0
                target_lang = goldendict.get('target_language', 'ru')
                if lang_code == target_lang:
                    return 2
                return 1

            txt_path = sorted(txt_files, key=txt_priority)[0]
        else:
            txt_path = input_path.with_suffix('.txt')
            if not txt_path.exists():
                txt_path = None
                warnings.append("Sibling source text file not found.")
    else:
        tsv_files = list(parent_dir.glob(f"{zid}-*.tsv"))
        if tsv_files:
            tsv_path = tsv_files[0]
        else:
            tsv_path = input_path.with_suffix('.tsv')
            if not tsv_path.exists():
                tsv_path = None
                warnings.append("Sibling TSV file not found.")
                
        txt_files = list(parent_dir.glob(f"{zid}-*.txt"))
        if txt_files:
            source_lang = None
            if tsv_path and len(tsv_path.suffixes) >= 2:
                source_lang = tsv_path.suffixes[-2].strip('.')
            
            def txt_priority(p):
                sufs = p.suffixes
                lang_code = sufs[-2].strip('.') if len(sufs) >= 2 else None
                if source_lang and lang_code == source_lang:
                    return 0
                target_lang = goldendict.get('target_language', 'ru')
                if lang_code == target_lang:
                    return 2
                return 1

            txt_path = sorted(txt_files, key=txt_priority)[0]
        else:
            if input_path.suffix == '.txt':
                txt_path = input_path
            else:
                txt_path = input_path.with_suffix('.txt')
                if not txt_path.exists():
                    txt_path = None
                    warnings.append("Sibling source text file not found.")
                    
    source_text = ""
    if txt_path and txt_path.exists():
        try:
            source_text = txt_path.read_text(encoding='utf-8')
        except Exception as e:
            warnings.append(f"Failed to read source text: {e}")
            
    headers = []
    data_rows = []
    if tsv_path and tsv_path.exists():
        try:
            _, headers, data_rows = load_tsv_rows(tsv_path)
        except Exception as e:
            warnings.append(f"Failed to read TSV: {e}")
            
    payload = {
        "source_text": source_text,
        "headers": headers,
        "data_rows": data_rows,
        "warnings": warnings,
        "tsv_path": str(tsv_path) if tsv_path else "",
        "txt_path": str(txt_path) if txt_path else ""
    }
    
    from b64util import encode
    response_str = json.dumps(payload)
    print(encode(response_str))

def cmd_desk(args):
    logger.info("Desk subcommand invoked")
    config, resolved_paths, goldendict = load_config(args.config)
    
    file_list = args.file if isinstance(args.file, list) else [args.file]
    
    if not args.no_gui:
        ahk_args = []
        zid_groups = {}
        non_zid_files = []
        for file_val in file_list:
            file_path = Path(file_val).resolve()
            if not file_path.exists():
                print_structured_error("INVALID_ARGS", f"File to analyze not found: {file_path}")
                continue
                
            match = re.match(r"^(\d{14})", file_path.name)
            if match:
                zid = match.group(1)
                if zid not in zid_groups:
                    zid_groups[zid] = []
                zid_groups[zid].append(file_path)
            else:
                non_zid_files.append(file_path)
                
        def priority(p):
            ext = p.suffix.lower()
            if ext == '.tsv': return 0
            if ext == '.txt': return 1
            return 2

        for zid, files in zid_groups.items():
            best_file = sorted(files, key=priority)[0]
            logger.info(f"File '{best_file.name}' is recognized as an existing session. Delegating to restore...")
            ahk_args.extend(["--restore", str(best_file)])
            
        for file_path in non_zid_files:
            is_tsv = file_path.suffix == '.tsv'
            if is_tsv:
                logger.info(f"File '{file_path.name}' is recognized as an existing session. Delegating to restore...")
                ahk_args.extend(["--restore", str(file_path)])
            else:
                ahk_args.extend(["--desk", str(file_path), "--text-mode", args.text_mode])
                
        if ahk_args:
            spawn_ahk(ahk_args, resolved_paths['base_dir'])
        return
        
    file_path = Path(file_list[0]).resolve()
    if not file_path.exists():
        print_structured_error("INVALID_ARGS", f"File to analyze not found: {file_path}")
        sys.exit(1)
        
    # Auto-detection: if it's a .tsv or starts with a 14-digit ZID, it's a restore session
    is_tsv = file_path.suffix == '.tsv'
    has_zid = bool(re.match(r"^\d{14}-", file_path.name))
    if is_tsv or has_zid:
        logger.info(f"File '{file_path.name}' is recognized as an existing session. Delegating to restore...")
        args.file = [str(file_path)]
        cmd_restore(args)
        return
        
    try:
        text = file_path.read_text(encoding='utf-8')
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Failed to read file: {e}")
        sys.exit(1)
        
    text_mode = getattr(args, 'text_mode', 'single')
    if text_mode == 'single' and '\n' in text.strip():
        text_mode = 'multi'
        
    if text_mode == 'multi':
        remove_empty = config.getboolean('settings', 'multi_mode_remove_empty_lines', fallback=True)
        clean_spaces = config.getboolean('settings', 'multi_mode_clean_spaces', fallback=True)
        if remove_empty or clean_spaces:
            new_lines = []
            for line in text.splitlines():
                if clean_spaces:
                    line = re.sub(r'[ \t]+', ' ', line).strip()
                if remove_empty and not line.strip():
                    continue
                new_lines.append(line)
            text = "\n".join(new_lines)
        
    lang = args.language
    if not lang:
        lang_match = re.search(r'\.([a-z]{2})\.(txt|srt)$', file_path.name)
        if lang_match:
            lang = lang_match.group(1)
        else:
            lang = config.get('settings', 'default_language', fallback='en')
            
    timestamp_id = datetime.now().strftime('%Y%m%d%H%M%S')
    
    try:
        theme_val = args.theme if hasattr(args, 'theme') else "dark"
        split_gap = config.getint('settings', 'split_gap_limit', fallback=60)
        html = run_render_flow(text, lang, timestamp_id, args.text_mode, config, resolved_paths, theme=theme_val, split_gap_limit=split_gap)
        from b64util import encode
        print(encode(html))
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Desk flow failed: {str(e)}")
        sys.exit(1)

def main():
    parser = argparse.ArgumentParser(description="Kardenwort Desk Orchestration Core")
    parser.add_argument("--config", default=None, help="Path to config.ini")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument("--debug", action="store_true", help="Debug logging")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # lookup
    p_lookup = subparsers.add_parser("lookup")
    p_lookup.add_argument("--text", required=True, help="Text to lookup")
    p_lookup.add_argument("--language", help="Source language code")
    p_lookup.add_argument("--target-lang", help="Target language code")
    p_lookup.add_argument("--format", choices=["html", "text", "combined"], help="Output format")
    p_lookup.add_argument("--text-mode", choices=["single", "multi", "auto", "sentence"], default="single", help="Text translation mode")
    p_lookup.add_argument("--sections", help="Comma-separated sections to render")
    p_lookup.add_argument("--lemma-columns", help="Comma-separated columns for the lemmas table")
    p_lookup.add_argument("--no-headings", action="store_true", help="Disable headings")
    p_lookup.add_argument("--disable-css", action="store_true", help="Disable outputting CSS styles in HTML")
    p_lookup.add_argument("--theme", choices=["dark", "light", "compact"], help="Theme (html format)")

    # render
    p_render = subparsers.add_parser("render")
    p_render.add_argument("--text", help="Selected text")
    p_render.add_argument("--language", required=True, help="Language code")
    p_render.add_argument("--zid", required=True, help="Session ZID")
    p_render.add_argument("--text-mode", choices=["single", "multi"], default="single")
    p_render.add_argument("--zoom", default=None, help="Zoom level for CSS scaling (falls back to config default_zoom)")
    p_render.add_argument("--tsv", default=None, help="Path to TSV file to render")
    p_render.add_argument("--theme", default="dark", choices=["dark", "light", "white"], help="Theme (dark or light or white)")
    p_render.add_argument("--split-gap-limit", type=int, default=None, help="Maximum source-word index distance allowed between parts of a split/separable verb construct")

    # export
    p_export = subparsers.add_parser("export")
    p_export.add_argument("--selection-manifest", required=True, help="Selection manifest path")
    p_export.add_argument("--language", required=True, help="Language code")

    # reprocess
    p_reprocess = subparsers.add_parser("reprocess")
    p_reprocess.add_argument("--selection-manifest", required=True, help="Selection manifest path")
    p_reprocess.add_argument("--language", required=True, help="Language code")

    # retext
    p_retext = subparsers.add_parser("retext")
    p_retext.add_argument("--selection-manifest", required=True, help="Selection manifest path")
    p_retext.add_argument("--language", required=True, help="Language code")
    p_retext.add_argument("--text-mode", default="single", choices=["single", "multi"], help="Text mode (single or multi)")

    # batch-worker
    p_batch_worker = subparsers.add_parser("batch-worker")
    p_batch_worker.add_argument("--tsv", required=True, help="Explicit TSV path")
    p_batch_worker.add_argument("--prompt", required=True, help="Prompt name")
    p_batch_worker.add_argument("--rows", required=True, help="Comma-separated list of row indices")

    # retext-worker
    p_retext_worker = subparsers.add_parser("retext-worker")
    p_retext_worker.add_argument("--tsv", required=True, help="Explicit TSV path")
    p_retext_worker.add_argument("--language", required=True, help="Language code")
    p_retext_worker.add_argument("--text-mode", default="single", choices=["single", "multi"], help="Text mode (single or multi)")

    # progressive-worker
    p_prog_worker = subparsers.add_parser("progressive-worker")
    p_prog_worker.add_argument("--tsv", required=True, help="Explicit TSV path")
    p_prog_worker.add_argument("--language", required=True, help="Language code")
    p_prog_worker.add_argument("--target-lang", required=True, help="Target language code")
    p_prog_worker.add_argument("--prompt", required=True, help="Prompt name")
    p_prog_worker.add_argument("--provider", required=True, help="Lemmas provider")
    p_prog_worker.add_argument("--word-empty", required=True, help="Word translations empty flag")
    p_prog_worker.add_argument("--text-mode", default="single", help="Text chunking mode")
    p_prog_worker.add_argument("--skip-intellifiller", action="store_true", help="Skip intellifiller phase")

    # edit-save
    p_edit = subparsers.add_parser("edit-save")
    p_edit.add_argument("--deltas", required=True, help="Deltas JSON file path")
    p_edit.add_argument("--zid", required=True, help="Session ZID")
    p_edit.add_argument("--language", help="Language code")
    p_edit.add_argument("--tsv", help="Explicit TSV path")

    # merge
    p_merge = subparsers.add_parser("merge")
    p_merge.add_argument("--files", nargs="+", required=True, help="List of TSV files to merge")
    p_merge.add_argument("--target", default="new", help="Merge target path, new, or first")

    # restore
    p_restore = subparsers.add_parser("restore")
    p_restore.add_argument("--file", nargs="+", required=True, help="Session file to restore")
    p_restore.add_argument("--no-gui", action="store_true", help="Do not spawn AHK window")

    # desk
    p_desk = subparsers.add_parser("desk")
    p_desk.add_argument("--file", nargs="+", required=True, help="Text file to analyze")
    p_desk.add_argument("--text-mode", choices=["single", "multi"], default="multi")
    p_desk.add_argument("--language", help="Language code")
    p_desk.add_argument("--no-gui", action="store_true", help="Do not spawn AHK window")
    p_desk.add_argument("--theme", default="dark", choices=["dark", "light", "white"], help="Theme (dark or light or white)")

    try:
        args = parser.parse_args()
    except SystemExit as e:
        if e.code != 0:
            print_structured_error("INVALID_ARGS", "Failed to parse command line arguments")
            sys.exit(1)
        sys.exit(0)

    setup_logging(verbose=args.verbose, debug=args.debug)

    commands = {
        "lookup": cmd_lookup,
        "render": cmd_render,
        "export": cmd_export,
        "reprocess": cmd_reprocess,
        "retext": cmd_retext,
        "batch-worker": cmd_reprocess_worker,
        "retext-worker": cmd_retext_worker,
        "progressive-worker": cmd_progressive_worker,
        "edit-save": cmd_edit_save,
        "merge": cmd_merge,
        "restore": cmd_restore,
        "desk": cmd_desk,
    }

    try:
        commands[args.command](args)
    except Exception as e:
        print_structured_error("DESK_FAILED", str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
