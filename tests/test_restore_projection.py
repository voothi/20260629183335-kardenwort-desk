"""Restore preserves configured spelling, exact-key precedence and cold SQL data."""
import configparser
import ast
from pathlib import Path

from kardenwort_desk import SqliteStorageAdapter
from tests.test_intent_architecture import complexity


def test_restore_projection_index_stays_pure_and_bounded():
    tree = ast.parse((Path(__file__).resolve().parents[1] / 'kardenwort_restore_projection.py').read_text())
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree))
    calls = {node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert not calls.intersection({'open', 'exec', 'eval', '__import__', 'print', 'input'})
    assert all(complexity(node) <= 7 for node in ast.walk(tree) if isinstance(node, ast.FunctionDef))


def test_restore_custom_field_case_precedence_and_column_order(tmp_path):
    mapping = tmp_path / 'mapping.ini'
    mapping.write_text('[fields]\nWordSource=\nTokenOrder=\nSentenceSourceIndex=\nCuStOm=\nNullField=\nAbsentCase=\n')
    config = configparser.ConfigParser()
    config.read_dict({'storage': {'backend': 'sqlite'}, 'settings': {}})
    paths = dict(base_dir=tmp_path, results_dir=tmp_path, anki_mapping_file=mapping,
                 sqlite_db_path=tmp_path/'restore.db', storage_backend='sqlite')
    adapter = SqliteStorageAdapter(config, paths, db_path=paths['sqlite_db_path'])
    zid = '20261113084919'
    fields = [
        {'custom': 'first', 'CUSTOM': 'second', 'CuStOm': 'exact',
         'nullfield': None, 'NULLFIELD': 'later', 'AbSeNtCaSe': 'first alias',
         'ABSENTCASE': 'second alias', 'LateNew': 'one'},
        {'custom': 'first', 'CuStOm': None, 'latenew': 'two', 'OtherNew': 'extra'},
        {'CuStOm': None, 'custom': 'later', 'LateNew': 'three'},
    ]
    adapter.db.save_session_bundle(
        dict(zid=zid, slug='restore-case', source_language='de', target_language='en',
             source_raw_text='eins zwei drei', text_mode='single'),
        [dict(session_zid=zid, sentence_index=1, sentence_source='eins zwei drei')],
        [dict(session_zid=zid, sentence_index=1, token_order=index,
              quotation=word, lemma=word, word_destination=word, extra_fields=extra)
         for index, (word, extra) in enumerate(zip(('eins', 'zwei', 'drei'), fields))])
    before = adapter.db.get_session_bundle(zid)
    restored = adapter.restore_session(zid)
    headers = restored['headers']
    assert headers == ['WordSource', 'TokenOrder', 'SentenceSourceIndex', 'CuStOm',
                       'NullField', 'AbsentCase', 'SentenceSource', 'LateNew', 'OtherNew']
    rows = [dict(zip(headers, row)) for row in restored['data_rows']]
    assert [row['CuStOm'] for row in rows] == ['exact', 'first', '']
    assert [row['NullField'] for row in rows] == ['', '', '']
    assert [row['AbsentCase'] for row in rows] == ['first alias', '', '']
    assert [row['LateNew'] for row in rows] == ['one', 'two', 'three']
    assert [row['OtherNew'] for row in rows] == ['', 'extra', '']
    assert [(row['SentenceSourceIndex'], row['TokenOrder']) for row in rows] == [('1', '0'), ('1', '1'), ('1', '2')]
    assert adapter.db.get_session_bundle(zid) == before
