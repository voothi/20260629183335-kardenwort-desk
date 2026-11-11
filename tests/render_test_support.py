"""Explicit SQLite prerequisites for renderer component contracts."""
import kardenwort_desk as desk


def seed_render_rows(config, paths, zid, text, language, headers, rows, text_mode='single'):
    adapter = desk.get_storage_adapter(config, paths)
    assert isinstance(adapter, desk.SqliteStorageAdapter), 'Declare an owned SQLite fixture'
    adapter.save_session(session_zid=zid, slug=desk.generate_slug(text), source_language=language,
        target_language=config.get('settings','default_target_language',fallback='ru'),
        text_mode=text_mode, source_raw_text=text, headers=headers, data_rows=rows,
        text_provenance='fixture:render-input')


def seed_render_tsv(config, paths, path, zid, text, language, text_mode='single'):
    # Explicit fixture import; the production renderer must read the DB normally.
    _, headers, rows = desk.load_tsv_rows(path)
    seed_render_rows(config, paths, zid, text, language, headers, rows, text_mode=text_mode)
