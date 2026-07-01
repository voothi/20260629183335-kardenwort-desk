import sys
import argparse
import json
import logging
import configparser
import os
import re
import subprocess
import tempfile
import contextlib
from pathlib import Path
from datetime import datetime

import text_tokenizer as tok

def is_contiguous_subsequence(sub, seq):
    if not sub or not seq:
        return False
    n = len(sub)
    m = len(seq)
    for i in range(m - n + 1):
        if seq[i:i+n] == sub:
            return True
    return False

class ConfigError(Exception):
    pass

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
            
    return config, resolved_paths

def load_kardenwort_config(kardenwort_workspace):
    kw_config = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    kw_config.read(kardenwort_workspace / "config.ini", encoding='utf-8')
    return kw_config

def load_anki_mapping(mapping_path):
    mapping = configparser.ConfigParser(allow_no_value=True, interpolation=None)
    mapping.optionxform = str # Preserve case for Anki field names!
    mapping.read(mapping_path, encoding='utf-8')
    return mapping

# Setup structured logging
class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
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
    settings.read(settings_path, encoding='utf-8')
    
    salt = settings.get('Security', 'Salt', fallback='')
    secrets_path_val = settings.get('Security', 'SecretsPath', fallback='')
    if not secrets_path_val:
        return None
        
    secrets_path = (settings_path.parent / secrets_path_val).resolve()
    if not secrets_path.exists():
        logger.warning(f"DeepL secrets file not found: {secrets_path}")
        return None
        
    secrets = configparser.ConfigParser()
    secrets.read(secrets_path, encoding='utf-8')
    
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
    bak_path = tsv_path.with_suffix('.tsv.bak')
    
    try:
        with open(temp_path, 'w', encoding='utf-8', newline='') as f:
            writer = csv_writer = re.sub(r'\r', '', '') # placeholder logic, use standard csv
            import csv
            writer = csv.writer(f, delimiter='\t', lineterminator='\n')
            for comment in comments:
                f.write(comment + '\n')
            writer.writerow(headers)
            for row in data_rows:
                writer.writerow(row)
                
        if tsv_path.exists():
            if bak_path.exists():
                os.remove(bak_path)
            os.rename(tsv_path, bak_path)
            
        try:
            os.rename(temp_path, tsv_path)
        except Exception as e:
            if bak_path.exists():
                os.rename(bak_path, tsv_path)
            raise e
            
        if bak_path.exists():
            try:
                os.remove(bak_path)
            except OSError:
                pass
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
    if config.getboolean('translation_providers', 'use_local_fork', fallback=True):
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
    if config.getboolean('translation_providers', 'use_local_fork', fallback=True):
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

def translate_text(text, source, target, config, resolved_paths, provider):
    if provider == 'google':
        try:
            return run_google_translation(text, source, target, config, resolved_paths)
        except Exception as e:
            logger.warning(f"Google translation failed: {e}. No failover.")
            raise
    elif provider == 'deepl':
        return run_deepl_translation(text, source, target, config, resolved_paths)
    elif provider in ('combined', 'intellifiller'):
        try:
            return run_google_translation(text, source, target, config, resolved_paths)
        except Exception as e:
            logger.warning(f"Google translation failed: {e}. Trying DeepL failover...")
            try:
                return run_deepl_translation(text, source, target, config, resolved_paths)
            except Exception as ex:
                logger.error(f"DeepL failover also failed: {ex}")
                raise ex
    else:
        raise Exception(f"Unsupported translation provider: {provider}")

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

def translate_source_text(text, source_lang, target_lang, text_mode, config, resolved_paths, provider):
    if text_mode == 'single':
        try:
            return {0: translate_text(text, source_lang, target_lang, config, resolved_paths, provider)}
        except Exception as e:
            logger.warning(f"Failed to translate main text: {e}")
            return {0: ""}
    else:
        # Phase 1: Collision Protection
        process_text = text.replace("[[S]]", "AHK_ESC_S_")
        process_text = process_text.replace("[[B]]", "AHK_ESC_B_")
        process_text = process_text.replace("[[N]]", "AHK_ESC_N_")

        # Phase 2: Tokenization
        process_text = process_text.replace("  ", " [[S]] ")
        if provider and provider.lower() == 'deepl':
            process_text = process_text.replace("\\", " [[B]] ")
        
        process_text = process_text.replace("\r\n", "[[N]]")
        process_text = process_text.replace("\n", "[[N]]")
        process_text = process_text.replace("\r", "[[N]]")
        
        try:
            import re
            translated_text = translate_text(process_text, source_lang, target_lang, config, resolved_paths, provider)
            
            if provider and provider.lower() == 'deepl':
                translated_text = re.sub(r'(?i)\s*\[\[\s*B\s*\]\]\s*', '\\\\', translated_text)
                
            translated_text = re.sub(r'(?i)\s*\[\[\s*S\s*\]\]\s*', '  ', translated_text)
            
            translated_text = translated_text.replace('\r\n', '')
            translated_text = translated_text.replace('\n', '')
            translated_text = translated_text.replace('\r', '')
            
            translated_text = re.sub(r'(?i)\[\[\s*N\s*\]\]', '\n', translated_text)
            
            translated_text = re.sub(r'(?i)AHK_ESC_S_', '[[S]]', translated_text)
            translated_text = re.sub(r'(?i)AHK_ESC_B_', '[[B]]', translated_text)
            translated_text = re.sub(r'(?i)AHK_ESC_N_', '[[N]]', translated_text)
            
            lines = text.splitlines()
            translated_lines = translated_text.split('\n')
            
            translations = {}
            trans_idx = 0
            for idx in range(len(lines)):
                if not lines[idx].strip():
                    translations[idx] = ""
                else:
                    while trans_idx < len(translated_lines) and not translated_lines[trans_idx].strip():
                        trans_idx += 1
                    
                    if trans_idx < len(translated_lines):
                        translations[idx] = translated_lines[trans_idx].strip()
                        trans_idx += 1
                    else:
                        translations[idx] = ""
            return translations
            
        except Exception as e:
            logger.warning(f"Failed to translate multi text block: {e}")
            lines = text.splitlines()
            return {idx: "" for idx in range(len(lines))}

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

def run_progressive_worker_async(tsv_path, language, target_lang, prompt_name, lemmas_provider, word_translations_empty):
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
        "--word-empty", str(word_translations_empty)
    ]
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
    
    if sys.platform == 'win32':
        creationflags = 0x00000200 | 0x00000008
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

def run_render_flow(text, language, zid, text_mode, config, resolved_paths, zoom_level="100", theme="dark"):
    kardenwort_workspace = resolved_paths['kardenwort_workspace']
    kw_config = load_kardenwort_config(kardenwort_workspace)
    
    results_dir_name = kw_config.get('project_structure', 'generated_results_dir', fallback='results')
    results_dir = (kardenwort_workspace / results_dir_name).resolve()
    results_dir.mkdir(parents=True, exist_ok=True)
    
    slug = generate_slug(text)
    working_tsv_path = results_dir / f"{zid}-{slug}.{language}.tsv"
    
    # Clean up any leftover update.js from previous sessions to avoid polling stale data
    update_js_path = working_tsv_path.with_suffix('.update.js')
    if update_js_path.exists():
        try:
            os.remove(update_js_path)
        except OSError:
            pass
            
    source_text_path = results_dir / f"{zid}-{slug}.{language}.txt"
    
    save_source_text = config.getboolean('settings', 'save_source_text', fallback=True)
    if save_source_text and not source_text_path.exists():
        source_text_path.write_text(text, encoding='utf-8')
        
    mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
    fields = list(mapping['fields'].keys())
    field_mapping = dict(mapping['fields_mapping.word'])
    
    if not working_tsv_path.exists():
        lemma_index_rel = config.get('languages', f'{language}_lemma_index')
        lemma_override_rel = config.get('languages', f'{language}_lemma_override')
        
        lemma_index_file = kardenwort_workspace / lemma_index_rel
        lemma_override_file = kardenwort_workspace / lemma_override_rel
        
        python_exe = resolved_paths['kardenwort_python']
        kardenwort_script = kardenwort_workspace / "src" / "kardenwort" / "core" / "kardenwort.py"
        
        text_file_to_pass = source_text_path
        if not save_source_text:
            temp_file = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', encoding='utf-8', delete=False)
            text_to_write = text
            if text_mode == 'single':
                text_to_write = " ".join([line.strip() for line in text.splitlines() if line.strip()])
            temp_file.write(text_to_write)
            temp_file.close()
            text_file_to_pass = Path(temp_file.name)
            
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
            "--text1-file", str(text_file_to_pass)
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
        finally:
            if not save_source_text and 'temp_file' in locals():
                try:
                    os.remove(temp_file.name)
                except OSError:
                    pass
                    
    comments, headers, data_rows = load_tsv_rows(working_tsv_path)
    target_lang = config.get('settings', 'default_target_language', fallback='ru')
    progressive_loading = config.getboolean('settings', 'progressive_loading', fallback=False)
    llm_filled = is_tsv_llm_filled(headers, data_rows, mapping)
    
    main_text_provider = config.get('translation_providers', 'main_text_translation', fallback='combined')
    lemmas_provider = config.get('translation_providers', 'lemmas_translation', fallback='combined')
    
    role_fields = {role: field for field, role in mapping['desk_columns'].items() if field in headers}
    if 'WordSourceMorphologyAI' in headers and 'morphology' not in role_fields:
        role_fields['morphology'] = 'WordSourceMorphologyAI'
    if 'WordSourceIPA' in headers and 'ipa' not in role_fields:
        role_fields['ipa'] = 'WordSourceIPA'
    if 'DeskSelected' in headers and 'selected' not in role_fields:
        role_fields['selected'] = 'DeskSelected'
        
    col_highlighted = headers.index(role_fields['selected']) if 'selected' in role_fields and role_fields['selected'] in headers else -1
    col_sentence_dest = headers.index(role_fields['sentence_destination']) if 'sentence_destination' in role_fields else -1
    col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields else -1
    col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields else -1
    col_inflected = headers.index(role_fields['inflected']) if 'inflected' in role_fields else -1
    
    sentence_translated = False
    if col_sentence_dest != -1:
        if any(len(row) > col_sentence_dest and row[col_sentence_dest].strip() for row in data_rows):
            sentence_translated = True
            
    if not sentence_translated:
        sentence_translations_raw = translate_source_text(text, language, target_lang, text_mode, config, resolved_paths, main_text_provider)
        col_index = headers.index('SentenceSourceIndex') if 'SentenceSourceIndex' in headers else -1
        
        content_to_absolute = {}
        if text_mode != 'single':
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
            
            abs_idx = content_to_absolute.get(content_line_idx, 0) if text_mode != 'single' else 0
            
            if col_sentence_dest != -1:
                while len(row) <= col_sentence_dest:
                    row.append("")
                row[col_sentence_dest] = sentence_translations_raw.get(abs_idx, "")
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
        if text_mode == 'single':
            sentence_translations[0] = " ".join(translation_lines)
        else:
            for a_idx, ln in enumerate(translation_lines):
                sentence_translations[a_idx] = ln
            # fill missing lines just in case
            for a_idx in range(len(text.splitlines())):
                if a_idx not in sentence_translations:
                    sentence_translations[a_idx] = ""
    else:
        if text_mode == 'single':
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
    if save_translation_text and sentence_translations:
        translation_text_path = results_dir / f"{zid}-{slug}.{target_lang}.txt"
        if not translation_text_path.exists():
            max_idx = max(sentence_translations.keys())
            translation_lines = [sentence_translations.get(i, "").strip() for i in range(max_idx + 1)]
            if text_mode == 'single':
                translation_text_out = " ".join(line for line in translation_lines if line)
            else:
                translation_text_out = "\n".join(translation_lines)
            translation_text_path.write_text(translation_text_out, encoding='utf-8')
            
    word_translations_empty = True
    if col_word_dest != -1:
        if any(len(row) > col_word_dest and row[col_word_dest].strip() for row in data_rows):
            word_translations_empty = False
            
    if not llm_filled:
        prompt_name = config.get('languages', f'{language}_prompt')
        
        if progressive_loading:
            # Stage 2: Offload lemma translation and intellifiller to background
            run_progressive_worker_async(working_tsv_path, language, target_lang, prompt_name, lemmas_provider, word_translations_empty)
        else:
            if lemmas_provider == 'intellifiller':
                run_headless_intellifiller(working_tsv_path, prompt_name, config, resolved_paths)
                comments, headers, data_rows = load_tsv_rows(working_tsv_path)
                
            elif lemmas_provider == 'combined':
                if word_translations_empty:
                    lemmas_to_translate = list(set(row[col_lemma] for row in data_rows if len(row) > col_lemma and row[col_lemma].strip()))
                    lemma_translations = translate_lemmas_fast_path(lemmas_to_translate, language, target_lang, config, resolved_paths, 'combined')
                    
                    for row in data_rows:
                        if col_lemma != -1 and len(row) > col_lemma:
                            lemma_val = row[col_lemma]
                            if col_word_dest != -1:
                                while len(row) <= col_word_dest:
                                    row.append("")
                                row[col_word_dest] = lemma_translations.get(lemma_val, "")
                    with file_lock(working_tsv_path):
                        save_tsv_rows_safely(working_tsv_path, comments, headers, data_rows)
                        
                run_headless_intellifiller_async(working_tsv_path, prompt_name, config, resolved_paths)
                
            elif lemmas_provider in ('google', 'deepl'):
                if word_translations_empty:
                    lemmas_to_translate = list(set(row[col_lemma] for row in data_rows if len(row) > col_lemma and row[col_lemma].strip()))
                    lemma_translations = translate_lemmas_fast_path(lemmas_to_translate, language, target_lang, config, resolved_paths, lemmas_provider)
                    
                    for row in data_rows:
                        if col_lemma != -1 and len(row) > col_lemma:
                            lemma_val = row[col_lemma]
                            if col_word_dest != -1:
                                while len(row) <= col_word_dest:
                                    row.append("")
                                row[col_word_dest] = lemma_translations.get(lemma_val, "")
                    with file_lock(working_tsv_path):
                        save_tsv_rows_safely(working_tsv_path, comments, headers, data_rows)
                    
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

    natively_paired_rows = set()
    for row_id, row in enumerate(data_rows):
        inflected_val = row[col_inflected] if col_inflected != -1 and len(row) > col_inflected else ""
        if inflected_val:
            inf_words = [tok.utf8_to_lower("".join(ch for ch in p if ch.isalnum() or ch == "'"))
                         for p in re.findall(r"[\w']+", inflected_val)]
            inf_words = [w for w in inf_words if w]
            if len(inf_words) > 1:
                if not is_contiguous_subsequence(inf_words, source_word_cleans):
                    natively_paired_rows.add(row_id)
            
    paired_tokens = set()
    for cand, r_ids in token_to_rows.items():
        if any(r in natively_paired_rows for r in r_ids):
            paired_tokens.add(cand)
            
    paired_rows = set()
    for row_id in range(len(data_rows)):
        candidates = row_candidates.get(row_id, set())
        if any(cand in paired_tokens for cand in candidates):
            paired_rows.add(row_id)
    
    span_htmls = []
    for token in source_tokens:
        tok_text = token["text"]
        text_escaped = tok_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        
        if token["is_word"]:
            lower_clean = token.get("lower_clean", "")
            mapped_rows = token_to_rows.get(lower_clean, [])
            
            classes = ["word"]
            if mapped_rows:
                is_paired = any(r_idx in paired_rows for r_idx in mapped_rows)
                if is_paired:
                    classes.append("highlight-purple")
                else:
                    classes.append("highlight-orange")
            else:
                classes.append("not-connected")
            classes_str = " ".join(classes)
            span_htmls.append(
                f'<span class="{classes_str}" data-word-idx="{token["visual_idx"]}" '
                f'data-lower-clean="{lower_clean}">{text_escaped}</span>'
            )
        else:
            if tok_text in ("\\N", "\\n", "\n"):
                span_htmls.append("<br>")
            else:
                span_htmls.append(text_escaped)
                
    source_html = "".join(span_htmls)
    
    sentence_htmls = []
    for idx in sorted(sentence_translations.keys()):
        trans = sentence_translations[idx]
        if trans:
            sentence_htmls.append(f"<div>{trans}</div>")
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
            f'<td class="{inflected_class}" data-col="WordSourceInflectedForm">{inflected_val}</td>'
            f'<td class="{lemma_class}" data-col="WordSource">{lemma_val}</td>'
            f'<td class="{trans_class}" data-col="WordDestination">{trans_val}</td>'
            f'<td>{ipa_val}</td>'
            f'<td><div class="scrollable-cell">{morph_val}</div></td>'
            f'</tr>'
        )
    table_rows_html = "\n".join(table_rows)
    
    token_manifest = []
    for token in source_tokens:
        tok_data = {
            "text": token["text"],
            "is_word": token["is_word"],
            "visual_idx": token["visual_idx"]
        }
        if token["is_word"] and "lower_clean" in token:
            tok_data["lower_clean"] = token["lower_clean"]
            mapped_rows = token_to_rows.get(token["lower_clean"], [])
            if mapped_rows:
                tok_data["row_ids"] = mapped_rows
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
    font-size: 15px;
    color: {table_text};
    font-style: italic;
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
  body:not(.maximized) .scrollable-cell {
    overflow-x: auto;
    white-space: nowrap;
    max-width: 250px;
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
<script id="progressive-loading" type="text/plain">{str(progressive_loading).lower()}</script>

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
            var progEl = document.getElementById('progressive-loading');
            if (progEl && (progEl.textContent || progEl.innerText) === 'true') {
                isProgressive = true;
            }
        } catch(e) {}
        
        window.receiveUpdate = function(data) {
            for (var rowId in data) {
                if (data.hasOwnProperty(rowId)) {
                    var tr = document.querySelector('tr[data-row-id="' + rowId + '"]');
                    if (tr) {
                        var tds = tr.getElementsByTagName('td');
                        var rowData = data[rowId];
                        if (tds.length >= 5) {
                            if (!tds[2].classList.contains('dirty') && rowData.trans) tds[2].textContent = rowData.trans;
                            if (!tds[3].classList.contains('dirty') && rowData.ipa) tds[3].textContent = rowData.ipa;
                            if (!tds[4].classList.contains('dirty') && rowData.morph) {
                                var div = tds[4].querySelector('.scrollable-cell');
                                if (div) div.innerHTML = rowData.morph;
                            }
                        }
                    }
                }
            }
        };

        if (isProgressive) {
            var tsvPathStr = document.getElementById('tsv-path').textContent || "";
            if (tsvPathStr) {
                var baseName = tsvPathStr.replace(/\\.tsv$/i, '');
                var jsUrl = "file:///" + baseName.replace(/\\\\/g, '/') + ".update.js";
                setInterval(function() {
                    var script = document.createElement('script');
                    script.src = jsUrl + "?t=" + new Date().getTime();
                    script.onload = function() { script.parentNode.removeChild(script); };
                    script.onerror = function() { script.parentNode.removeChild(script); };
                    document.head.appendChild(script);
                }, 2000);
            }
        }
        
        var sourceContainer = document.getElementById('source-container');
        var spans = sourceContainer ? sourceContainer.getElementsByTagName('span') : [];
        var tokenSpans = [];
        for (var i = 0; i < spans.length; i++) {
            if (spans[i].className.indexOf('word') !== -1) {
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

        function findTokenData(lowerClean) {
            for (var i = 0; i < tokenMap.length; i++) {
                var t = tokenMap[i];
                if (t.lower_clean === lowerClean && t.is_word) {
                    return t;
                }
            }
            return null;
        }
        
        function getWordTranslation(span) {
            var lowerClean = span.getAttribute('data-lower-clean');
            var tokenData = findTokenData(lowerClean);
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
                    e = e || window.event;
                    
                    if (e.button === 0) { // LMB
                        var lowerClean = span.getAttribute('data-lower-clean');
                        var tokenData = findTokenData(lowerClean);
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
                            var lc = s.getAttribute('data-lower-clean');
                            var td = findTokenData(lc);
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
                        dragSelectMode = true;
                        var start = Math.min(parseInt(lastClickedRowId), parseInt(rowId));
                        var end = Math.max(parseInt(lastClickedRowId), parseInt(rowId));
                        for (var j = start; j <= end; j++) {
                            selectedRowIdsMap[String(j)] = true;
                        }
                        lastClickedRowId = rowId;
                    } else {
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
                    e = e || window.event;
                    if (isDragSelecting) {
                        if (e.buttons !== undefined && (e.buttons & 1) === 0) {
                            isDragSelecting = false;
                            notifyAHKSelection();
                            return;
                        }
                        dragOccurred = true;
                        var rowId = parseInt(row.getAttribute('data-row-id'));
                        var rowIdStr = String(rowId);
                        
                        if (dragSelectMode) {
                            selectedRowIdsMap[rowIdStr] = true;
                        } else {
                            delete selectedRowIdsMap[rowIdStr];
                        }
                        
                        focusedRowId = rowId;
                        updateRowStyles();
                        updateBidirectionalHighlights();
                    }
                });
                
                var tds = row.getElementsByTagName('td');
                for (var j = 0; j < tds.length; j++) {
                    if (tds[j].className.indexOf('editable') !== -1) {
                        (function(cell) {
                            addEvent(cell, 'click', function(e) {
                                lastClickedCell = cell;
                            });
                            addEvent(cell, 'mouseover', function(e) {
                                lastHoveredCell = cell;
                            });
                            addEvent(cell, 'mouseout', function(e) {
                                if (lastHoveredCell === cell) {
                                    lastHoveredCell = null;
                                }
                            });
                            addEvent(cell, 'dblclick', function() {
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
                            if (tds[k].className.indexOf('editable') !== -1) {
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
        }
        
        window.clearAllSelectionsAndNotify = function() {
            clearAllSelections();
            updateBidirectionalHighlights();
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
                var isSelected = selectedRowIdsMap.hasOwnProperty(rowIdStr);
                if (isSelected) {
                    if (row.className.indexOf('selected') === -1) {
                        row.className += ' selected';
                    }
                } else {
                    row.className = row.className.replace(/selected/g, '').replace(/\\s+/g, ' ').replace(/^\\s+|\\s+$/g, '');
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
                span.className = span.className.replace(/highlight-orange-active/g, '')
                                               .replace(/highlight-purple-active/g, '')
                                               .replace(/\\s+/g, ' ')
                                               .replace(/^\\s+|\\s+$/g, '');
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
                            if (span.className.indexOf('highlight-purple') !== -1) {
                                if (span.className.indexOf('highlight-purple-active') === -1) {
                                    span.className += ' highlight-purple-active';
                                }
                            } else if (span.className.indexOf('highlight-orange') !== -1) {
                                if (span.className.indexOf('highlight-orange-active') === -1) {
                                    span.className += ' highlight-orange-active';
                                }
                            }
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
            
            var originalValue = cell.textContent || cell.innerText || "";
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
            cell.appendChild(input);
            input.focus();
            try {
                input.select();
            } catch(e) {}
            
            window.cancelActiveEdit = function() {
                cell.innerHTML = '';
                cell.appendChild(document.createTextNode(originalValue));
                cell.className = cell.className.replace(/\\s*editing\\b/g, '');
                window.cancelActiveEdit = null;
            };
            
            function commit() {
                var newValue = input.value;
                cell.innerHTML = '';
                cell.appendChild(document.createTextNode(newValue));
                cell.className = cell.className.replace(/\\s*editing\\b/g, '');
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
                        if (tds[k].className.indexOf('editable') !== -1) {
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
                action.cell.appendChild(document.createTextNode(action.newValue));
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
                action.cell.appendChild(document.createTextNode(action.oldValue));
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
                if (tds[k].className.indexOf('dirty') !== -1) {
                    tds[k].className = tds[k].className.replace(/\bdirty\b/g, '');
                }
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
                                    if (tdst[m].className.indexOf('dirty') === -1) {
                                        tdst[m].className += ' dirty';
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
                if (tds[k].className.indexOf('dirty') !== -1) {
                    tds[k].className = tds[k].className.replace(/\bdirty\b/g, '');
                }
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
                            if (tds[j].className.indexOf('editable') !== -1) {
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
                if (curr.className && curr.className.indexOf('scrollable-cell') !== -1) {
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
    html_page = html_page.replace("{language}", language)
    html_page = html_page.replace("{theme_class}", f"theme-{theme}")
    html_page = html_page.replace("{source_white_space}", "pre-wrap" if text_mode == "multi" else "normal")
    
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

def cmd_render(args):
    logger.info("Render subcommand invoked", extra={"zid": args.zid})
    config, resolved_paths = load_config(args.config)
    
    if not args.text:
        if not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            print_structured_error("INVALID_ARGS", "No text provided to render")
            sys.exit(1)
    else:
        text = args.text
        
    try:
        html = run_render_flow(text, args.language, args.zid, args.text_mode, config, resolved_paths, args.zoom, args.theme)
        from b64util import encode
        print(encode(html))
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Render failed: {str(e)}")
        sys.exit(1)

def cmd_export(args):
    logger.info("Export subcommand invoked")
    config, resolved_paths = load_config(args.config)
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
        logger.warning("No rows selected for export.")
        print("Warning: No rows selected. Export skipped.")
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
        
    exported_rows = []
    for row_id in selected_rows:
        if 0 <= row_id < len(data_rows):
            exported_rows.append(data_rows[row_id])
        else:
            logger.warning(f"Export row index {row_id} is out of bounds (total rows: {len(data_rows)})")
            
    if not exported_rows:
        print("Warning: None of the selected row indices were valid.")
        sys.exit(0)
        
    fav_dir = resolved_paths['favorites_output_dir']
    fav_dir.mkdir(parents=True, exist_ok=True)
    
    dest_filename = f"favorites-{tsv_path.name}"
    dest_path = fav_dir / dest_filename
    
    try:
        with file_lock(dest_path):
            save_tsv_rows_safely(dest_path, comments, headers, exported_rows)
        logger.info(f"Exported favorites to {dest_path}")
        
        send_to_anki = config.getboolean('settings', 'send_to_anki_after_export', fallback=False)
        if send_to_anki:
            detach = config.getboolean('settings', 'detach_import_on_send', fallback=True)
            if detach:
                pid, log_path = run_detached_import(dest_path, config, resolved_paths, zid)
                response = {
                    "import_started": True,
                    "pid": pid,
                    "log": log_path,
                    "tsv": str(dest_path),
                    "note": "safe to close the window"
                }
                print(json.dumps(response))
            else:
                success, output = run_synchronous_import(dest_path, config, resolved_paths)
                if success:
                    print(json.dumps({"import_complete": True, "output": output}))
                else:
                    print_structured_error("IMPORT_FAILED", "Anki import failed synchronously", {"details": output})
                    sys.exit(1)
        else:
            print(f"SUCCESS: Exported to {dest_path}")
    except Exception as e:
        print_structured_error("EXPORT_FAILED", f"Failed to save exported favorites: {e}")
        sys.exit(1)

def cmd_reprocess(args):
    logger.info("Reprocess subcommand invoked")
    config, resolved_paths = load_config(args.config)
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
    print(json.dumps({"reprocess_started": True, "rows": cleared_count}))

def cmd_reprocess_worker(args):
    config, resolved_paths = load_config(args.config)
    batch_size = config.getint('settings', 'intellifiller_batch_size', fallback=5)
    tsv_path = Path(args.tsv)
    
    rows_str = args.rows
    if not rows_str:
        return
        
    selected_rows = [int(r.strip()) for r in rows_str.split(',') if r.strip()]
    
    for i in range(0, len(selected_rows), batch_size):
        batch = selected_rows[i:i + batch_size]
        logger.info(f"Running IntelliFiller for batch {i // batch_size + 1}: {len(batch)} rows.")
        run_headless_intellifiller(tsv_path, args.prompt, config, resolved_paths, selected_rows=batch)

def write_update_js(tsv_path, data_rows, headers, role_fields):
    update_js_path = tsv_path.with_suffix('.update.js')
    
    col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields else -1
    col_inflected = headers.index(role_fields['inflected']) if 'inflected' in role_fields else -1
    col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields else -1
    col_morph = headers.index(role_fields['morphology']) if 'morphology' in role_fields else -1
    col_ipa = headers.index(role_fields['ipa']) if 'ipa' in role_fields else -1
    
    update_data = {}
    for row_id, row in enumerate(data_rows):
        lemma_val = row[col_lemma] if col_lemma != -1 and len(row) > col_lemma else ""
        inflected_val = row[col_inflected] if col_inflected != -1 and len(row) > col_inflected else ""
        trans_val = row[col_word_dest] if col_word_dest != -1 and len(row) > col_word_dest else ""
        morph_val = row[col_morph] if col_morph != -1 and len(row) > col_morph else ""
        ipa_val = row[col_ipa] if col_ipa != -1 and len(row) > col_ipa else ""
        
        if trans_val or ipa_val or morph_val:
            update_data[row_id] = {
                "trans": trans_val,
                "ipa": ipa_val,
                "morph": morph_val
            }
    
    js_content = f"if (typeof window.receiveUpdate === 'function') {{ window.receiveUpdate({json.dumps(update_data)}); }}"
    temp_path = update_js_path.with_suffix('.update.js.tmp')
    with open(temp_path, 'w', encoding='utf-8') as f:
        f.write(js_content)
    try:
        if update_js_path.exists():
            os.remove(update_js_path)
        os.rename(temp_path, update_js_path)
    except Exception:
        pass

def cmd_progressive_worker(args):
    logger.info("Progressive-worker subcommand invoked")
    config, resolved_paths = load_config(args.config)
    tsv_path = Path(args.tsv)
    
    if not tsv_path.exists():
        return
        
    comments, headers, data_rows = load_tsv_rows(tsv_path)
    mapping = load_anki_mapping(resolved_paths['anki_mapping_file'])
    role_fields = {role: field for field, role in mapping['desk_columns'].items() if field in headers}
    
    col_lemma = headers.index(role_fields['lemma']) if 'lemma' in role_fields else -1
    col_word_dest = headers.index(role_fields['word_translation']) if 'word_translation' in role_fields else -1
    
    word_translations_empty = args.word_empty.lower() == 'true'
    
    if args.provider in ('combined', 'google', 'deepl'):
        if word_translations_empty:
            lemmas_to_translate = list(set(row[col_lemma] for row in data_rows if len(row) > col_lemma and row[col_lemma].strip()))
            provider = 'combined' if args.provider == 'combined' else args.provider
            lemma_translations = translate_lemmas_fast_path(lemmas_to_translate, args.language, args.target_lang, config, resolved_paths, provider)
            
            with file_lock(tsv_path):
                comments, headers, current_rows = load_tsv_rows(tsv_path)
                for row in current_rows:
                    if col_lemma != -1 and len(row) > col_lemma:
                        lemma_val = row[col_lemma]
                        if col_word_dest != -1:
                            while len(row) <= col_word_dest:
                                row.append("")
                            if not row[col_word_dest].strip():
                                row[col_word_dest] = lemma_translations.get(lemma_val, "")
                save_tsv_rows_safely(tsv_path, comments, headers, current_rows)
                data_rows = current_rows
            write_update_js(tsv_path, data_rows, headers, role_fields)
                
        if args.provider == 'combined':
            run_headless_intellifiller(tsv_path, args.prompt, config, resolved_paths)
            comments, headers, data_rows = load_tsv_rows(tsv_path)
            write_update_js(tsv_path, data_rows, headers, role_fields)
            
    elif args.provider == 'intellifiller':
        run_headless_intellifiller(tsv_path, args.prompt, config, resolved_paths)
        comments, headers, data_rows = load_tsv_rows(tsv_path)
        write_update_js(tsv_path, data_rows, headers, role_fields)




def cmd_edit_save(args):
    logger.info("Edit-save subcommand invoked", extra={"zid": args.zid})
    config, resolved_paths = load_config(args.config)
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
            
    try:
        with file_lock(tsv_path):
            save_tsv_rows_safely(tsv_path, comments, headers, data_rows)
        print("SUCCESS")
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Failed to save working TSV: {e}")
        sys.exit(1)

def cmd_merge(args):
    logger.info("Merge subcommand invoked")
    config, resolved_paths = load_config(args.config)
    
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
    config, resolved_paths = load_config(args.config)
    
    file_val = args.file[0] if isinstance(args.file, list) else args.file
    input_path = Path(file_val).resolve()
    if not input_path.exists():
        print_structured_error("INVALID_ARGS", f"File to restore not found: {input_path}")
        sys.exit(1)
        
    if not args.no_gui:
        spawn_ahk(["--restore", str(input_path)], resolved_paths['base_dir'])
        return
        
    zid = extract_zid(input_path)
    parent_dir = input_path.parent
    
    tsv_path = None
    txt_path = None
    warnings = []
    
    if input_path.suffix == '.tsv':
        tsv_path = input_path
        txt_files = list(parent_dir.glob(f"{zid}-*.txt"))
        if txt_files:
            txt_path = txt_files[0]
        else:
            txt_path = input_path.with_suffix('.txt')
            if not txt_path.exists():
                txt_path = None
                warnings.append("Sibling source text file not found.")
    else:
        txt_path = input_path
        tsv_files = list(parent_dir.glob(f"{zid}-*.tsv"))
        if tsv_files:
            tsv_path = tsv_files[0]
        else:
            lang = config.get('settings', 'default_language', fallback='en')
            matches = list(parent_dir.glob(f"{zid}-*.tsv"))
            if matches:
                tsv_path = matches[0]
            else:
                tsv_path = input_path.with_suffix('.tsv')
                if not tsv_path.exists():
                    tsv_path = None
                    warnings.append("Sibling TSV file not found.")
                    
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
    config, resolved_paths = load_config(args.config)
    
    file_val = args.file[0] if isinstance(args.file, list) else args.file
    file_path = Path(file_val).resolve()
    if not file_path.exists():
        print_structured_error("INVALID_ARGS", f"File to analyze not found: {file_path}")
        sys.exit(1)
        
    # Auto-detection: if it's a .tsv or starts with a 14-digit ZID, it's a restore session
    is_tsv = file_path.suffix == '.tsv'
    has_zid = bool(re.match(r"^\d{14}-", file_path.name))
    if is_tsv or has_zid:
        logger.info(f"File '{file_path.name}' is recognized as an existing session. Delegating to restore...")
        cmd_restore(args)
        return
        
    if not args.no_gui:
        spawn_ahk(["--desk", str(file_path), "--text-mode", args.text_mode], resolved_paths['base_dir'])
        return
        
    try:
        text = file_path.read_text(encoding='utf-8')
    except Exception as e:
        print_structured_error("DESK_FAILED", f"Failed to read file: {e}")
        sys.exit(1)
        
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
        html = run_render_flow(text, lang, timestamp_id, args.text_mode, config, resolved_paths, theme=theme_val)
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

    # render
    p_render = subparsers.add_parser("render")
    p_render.add_argument("--text", help="Selected text")
    p_render.add_argument("--language", required=True, help="Language code")
    p_render.add_argument("--zid", required=True, help="Session ZID")
    p_render.add_argument("--text-mode", choices=["single", "multi"], default="single")
    p_render.add_argument("--zoom", default="100", help="Zoom level for CSS scaling")
    p_render.add_argument("--theme", default="dark", choices=["dark", "light", "white"], help="Theme (dark or light or white)")

    # export
    p_export = subparsers.add_parser("export")
    p_export.add_argument("--selection-manifest", required=True, help="Selection manifest path")
    p_export.add_argument("--language", required=True, help="Language code")

    # reprocess
    p_reprocess = subparsers.add_parser("reprocess")
    p_reprocess.add_argument("--selection-manifest", required=True, help="Selection manifest path")
    p_reprocess.add_argument("--language", required=True, help="Language code")

    # batch-worker
    p_batch_worker = subparsers.add_parser("batch-worker")
    p_batch_worker.add_argument("--tsv", required=True, help="Explicit TSV path")
    p_batch_worker.add_argument("--prompt", required=True, help="Prompt name")
    p_batch_worker.add_argument("--rows", required=True, help="Comma-separated list of row indices")

    # progressive-worker
    p_prog_worker = subparsers.add_parser("progressive-worker")
    p_prog_worker.add_argument("--tsv", required=True, help="Explicit TSV path")
    p_prog_worker.add_argument("--language", required=True, help="Language code")
    p_prog_worker.add_argument("--target-lang", required=True, help="Target language code")
    p_prog_worker.add_argument("--prompt", required=True, help="Prompt name")
    p_prog_worker.add_argument("--provider", required=True, help="Lemmas provider")
    p_prog_worker.add_argument("--word-empty", required=True, help="Word translations empty flag")

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
        "render": cmd_render,
        "export": cmd_export,
        "reprocess": cmd_reprocess,
        "batch-worker": cmd_reprocess_worker,
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
