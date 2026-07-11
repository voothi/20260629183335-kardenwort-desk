"""
Unit tests for the word-fill engine (20260711143647-word-fill-engine).
Tests: collect_candidate_files, score_wordfill_row, find_wordfill_match, apply_wordfill_to_rows.
"""
import pytest
import csv
import io
from pathlib import Path
from unittest.mock import patch

import kardenwort_desk as desk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_tsv(path: Path, headers: list, rows: list) -> None:
    """Write a minimal TSV file with given headers and rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8', newline='') as f:
        writer = csv.writer(f, delimiter='\t', lineterminator='\n')
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)


def _make_wordfill_cfg(tmp_path, scan_roots=None, search_depth=1,
                       data_mode='all', min_quality='any', effort='fallback',
                       max_scan_files=500, enabled=True):
    return {
        'enabled': enabled,
        'scan_roots': scan_roots or [tmp_path],
        'search_depth': search_depth,
        'data_mode': data_mode,
        'min_quality': min_quality,
        'effort': effort,
        'max_scan_files': max_scan_files,
    }


# ---------------------------------------------------------------------------
# 5.1  collect_candidate_files
# ---------------------------------------------------------------------------

class TestCollectCandidateFiles:

    def test_depth0_excludes_subdirs(self, tmp_path):
        """Depth 0 must not descend into subdirectories."""
        # Root-level file
        root_file = tmp_path / "20260701120000-session.en.tsv"
        root_file.touch()
        # Subdir file
        subdir = tmp_path / "20260702120000-archive"
        subdir.mkdir()
        (subdir / "20260702120100-sub.en.tsv").touch()

        result = desk.collect_candidate_files([tmp_path], search_depth=0,
                                              data_mode='all', language='en')
        names = [f.name for f in result]
        assert "20260701120000-session.en.tsv" in names
        assert "20260702120100-sub.en.tsv" not in names

    def test_depth1_includes_subdirs(self, tmp_path):
        """Depth 1 must include files one subdirectory deep."""
        root_file = tmp_path / "20260701120000-session.en.tsv"
        root_file.touch()
        subdir = tmp_path / "20260702120000-archive"
        subdir.mkdir()
        sub_file = subdir / "20260702120100-sub.en.tsv"
        sub_file.touch()

        result = desk.collect_candidate_files([tmp_path], search_depth=1,
                                              data_mode='all', language='en')
        names = [f.name for f in result]
        assert "20260701120000-session.en.tsv" in names
        assert "20260702120100-sub.en.tsv" in names

    def test_data_mode_merged_filters_non_merged(self, tmp_path):
        """data_mode='merged' should only include files whose name contains '-merged.'."""
        (tmp_path / "20260701120000-session.en.tsv").touch()
        (tmp_path / "20260701130000-merged.en.tsv").touch()

        result = desk.collect_candidate_files([tmp_path], search_depth=0,
                                              data_mode='merged', language='en')
        names = [f.name for f in result]
        assert "20260701130000-merged.en.tsv" in names
        assert "20260701120000-session.en.tsv" not in names

    def test_data_mode_all_includes_session_files(self, tmp_path):
        """data_mode='all' should include both session and merged files."""
        (tmp_path / "20260701120000-session.en.tsv").touch()
        (tmp_path / "20260701130000-merged.en.tsv").touch()

        result = desk.collect_candidate_files([tmp_path], search_depth=0,
                                              data_mode='all', language='en')
        names = [f.name for f in result]
        assert "20260701120000-session.en.tsv" in names
        assert "20260701130000-merged.en.tsv" in names

    def test_language_suffix_filter(self, tmp_path):
        """Files with a non-matching language suffix must be excluded."""
        (tmp_path / "20260701120000-session.en.tsv").touch()
        (tmp_path / "20260701120000-session.de.tsv").touch()

        result = desk.collect_candidate_files([tmp_path], search_depth=0,
                                              data_mode='all', language='en')
        names = [f.name for f in result]
        assert "20260701120000-session.en.tsv" in names
        assert "20260701120000-session.de.tsv" not in names

    def test_max_scan_files_cap(self, tmp_path, caplog):
        """max_scan_files cap should limit results and emit a warning."""
        for i in range(15):
            zid = f"202607{i+1:02d}120000"
            (tmp_path / f"{zid}-session.en.tsv").touch()

        import logging
        with caplog.at_level(logging.WARNING, logger='root'):
            result = desk.collect_candidate_files([tmp_path], search_depth=0,
                                                  data_mode='all', language='en',
                                                  max_scan_files=5)
        assert len(result) == 5
        assert any("max_scan_files" in rec.message or "most recent" in rec.message
                   for rec in caplog.records)

    def test_multiple_scan_roots(self, tmp_path):
        """All provided scan roots should be traversed."""
        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        root_a.mkdir()
        root_b.mkdir()
        (root_a / "20260701120000-a.en.tsv").touch()
        (root_b / "20260701130000-b.en.tsv").touch()

        result = desk.collect_candidate_files([root_a, root_b], search_depth=0,
                                              data_mode='all', language='en')
        names = [f.name for f in result]
        assert "20260701120000-a.en.tsv" in names
        assert "20260701130000-b.en.tsv" in names

    def test_sorted_newest_first(self, tmp_path):
        """Results must be sorted newest ZID first."""
        (tmp_path / "20260701000000-old.en.tsv").touch()
        (tmp_path / "20260710000000-new.en.tsv").touch()

        result = desk.collect_candidate_files([tmp_path], search_depth=0,
                                              data_mode='all', language='en')
        assert result[0].name.startswith("20260710")
        assert result[1].name.startswith("20260701")

    def test_merged_before_session_same_zid(self, tmp_path):
        """In data_mode='all', a merged file with the same ZID prefix must rank
        before a session file with the same ZID prefix."""
        (tmp_path / "20260710120000-session.en.tsv").touch()
        (tmp_path / "20260710120000-merged.en.tsv").touch()

        result = desk.collect_candidate_files([tmp_path], search_depth=0,
                                              data_mode='all', language='en')
        assert result[0].name == "20260710120000-merged.en.tsv"
        assert result[1].name == "20260710120000-session.en.tsv"

    def test_newer_session_beats_older_merged(self, tmp_path):
        """A session file from a later ZID must still rank above a merged file
        from an earlier ZID — ZID is the primary sort key."""
        (tmp_path / "20260710120000-merged.en.tsv").touch()   # older, merged
        (tmp_path / "20260711000000-session.en.tsv").touch()  # newer, session

        result = desk.collect_candidate_files([tmp_path], search_depth=0,
                                              data_mode='all', language='en')
        assert result[0].name.startswith("20260711")
        assert result[1].name.startswith("20260710")

    def test_language_strict_true_excludes_foreign(self, tmp_path):
        """language_strict=True must exclude files whose suffix doesn't match the language."""
        (tmp_path / "20260710120000-session.en.tsv").touch()
        (tmp_path / "20260710120000-session.de.tsv").touch()
        (tmp_path / "20260710120000-session.fr.tsv").touch()

        result = desk.collect_candidate_files([tmp_path], search_depth=0,
                                              data_mode='all', language='en',
                                              language_strict=True)
        names = [f.name for f in result]
        assert "20260710120000-session.en.tsv" in names
        assert "20260710120000-session.de.tsv" not in names
        assert "20260710120000-session.fr.tsv" not in names

    def test_language_strict_false_includes_all_languages(self, tmp_path):
        """language_strict=False must include .tsv files of any language suffix."""
        (tmp_path / "20260710120000-session.en.tsv").touch()
        (tmp_path / "20260710120000-session.de.tsv").touch()
        (tmp_path / "20260710120000-session.fr.tsv").touch()

        result = desk.collect_candidate_files([tmp_path], search_depth=0,
                                              data_mode='all', language='en',
                                              language_strict=False)
        names = [f.name for f in result]
        assert "20260710120000-session.en.tsv" in names
        assert "20260710120000-session.de.tsv" in names
        assert "20260710120000-session.fr.tsv" in names

    def test_language_strict_default_is_true(self, tmp_path):
        """Default value of language_strict must be True (strict matching)."""
        (tmp_path / "20260710120000-session.en.tsv").touch()
        (tmp_path / "20260710120000-session.de.tsv").touch()

        # Call without language_strict kwarg — default must behave as strict=True
        result = desk.collect_candidate_files([tmp_path], search_depth=0,
                                              data_mode='all', language='en')
        names = [f.name for f in result]
        assert "20260710120000-session.en.tsv" in names
        assert "20260710120000-session.de.tsv" not in names


# ---------------------------------------------------------------------------
# 5.2  score_wordfill_row
# ---------------------------------------------------------------------------

class TestScoreWordfillRow:

    BASE_HEADERS = ['WordSource', 'WordSourceIPA', 'WordSourceMorphologyAI', 'WordDestination']

    def test_tier_2_both_filled(self):
        row = ['run', '/rʌn/', 'verb, irregular', 'laufen']
        assert desk.score_wordfill_row(row, self.BASE_HEADERS) == 2

    def test_tier_1_only_ipa(self):
        row = ['run', '/rʌn/', '', 'laufen']
        assert desk.score_wordfill_row(row, self.BASE_HEADERS) == 1

    def test_tier_1_only_morphology(self):
        row = ['run', '', 'verb, irregular', 'laufen']
        assert desk.score_wordfill_row(row, self.BASE_HEADERS) == 1

    def test_tier_0_neither(self):
        row = ['run', '', '', 'laufen']
        assert desk.score_wordfill_row(row, self.BASE_HEADERS) == 0

    def test_tier_0_missing_columns(self):
        headers = ['WordSource', 'WordDestination']
        row = ['run', 'laufen']
        assert desk.score_wordfill_row(row, headers) == 0

    def test_tier_0_short_row(self):
        row = ['run']  # shorter than headers
        assert desk.score_wordfill_row(row, self.BASE_HEADERS) == 0


# ---------------------------------------------------------------------------
# 5.3  find_wordfill_match
# ---------------------------------------------------------------------------

FULL_HEADERS = ['Quotation', 'WordSource', 'WordSourceInflectedForm',
                'WordSourceIPA', 'WordSourceMorphologyAI', 'WordDestination']


def _row(quotation='', lemma='', inflected='', ipa='', morph='', dest=''):
    return [quotation, lemma, inflected, ipa, morph, dest]


class TestFindWordfillMatch:

    def test_find_wordfill_effort_fallback(self, tmp_path):
        """When effort=fallback, it should return the highest quality sub-minimum match if minimum isn't met."""
        (tmp_path / "20260710120000-session.en.tsv").write_text("WordSource\tWordSourceIPA\tWordSourceMorphologyAI\tWordDestination\ncat\t\t\tKatze", encoding='utf-8')  # bare (0)
        (tmp_path / "20260709120000-session.en.tsv").write_text("WordSource\tWordSourceIPA\tWordSourceMorphologyAI\tWordDestination\ncat\t/kæt/\t\tKatze", encoding='utf-8')  # partial (1)
        (tmp_path / "20260708120000-session.en.tsv").write_text("WordSource\tWordSourceIPA\tWordSourceMorphologyAI\tWordDestination\ncat\t\t\tKatze", encoding='utf-8')  # bare (0)

        wordfill_cfg = {
            'enabled': True,
            'scan_roots': [tmp_path],
            'search_depth': 0,
            'data_mode': 'all',
            'min_quality': 'full',  # Requires tier 2 (IPA + Morphology)
            'effort': 'fallback',
            'language_strict': True,
            'max_scan_files': 10
        }
        # It should scan all three, fail to find 'full', but fallback to the 'partial' (1) from 0709
        result = desk.find_wordfill_match('cat', 'en', wordfill_cfg, None)
        assert result is not None
        assert result.get('WordSourceIPA') == '/kæt/'
        assert 'WordSourceMorphologyAI' not in result or result['WordSourceMorphologyAI'] == ''

    def test_find_wordfill_effort_strict(self, tmp_path):
        """When effort=strict, it should return None if minimum isn't met."""
        (tmp_path / "20260710120000-session.en.tsv").write_text("WordSource\tWordSourceIPA\tWordSourceMorphologyAI\tWordDestination\ncat\t\t\tKatze", encoding='utf-8')  # bare
        (tmp_path / "20260709120000-session.en.tsv").write_text("WordSource\tWordSourceIPA\tWordSourceMorphologyAI\tWordDestination\ncat\t/kæt/\t\tKatze", encoding='utf-8')  # partial

        wordfill_cfg = {
            'enabled': True,
            'scan_roots': [tmp_path],
            'search_depth': 0,
            'data_mode': 'all',
            'min_quality': 'full',
            'effort': 'strict',
            'language_strict': True,
            'max_scan_files': 10
        }
        # It should scan both, fail to find 'full', and return None because of strict
        result = desk.find_wordfill_match('cat', 'en', wordfill_cfg, None)
        assert result is None

    def test_disabled_returns_none(self, tmp_path):
        cfg = _make_wordfill_cfg(tmp_path, enabled=False)
        assert desk.find_wordfill_match('run', 'en', cfg) is None

    def test_empty_word_returns_none(self, tmp_path):
        cfg = _make_wordfill_cfg(tmp_path)
        assert desk.find_wordfill_match('   ', 'en', cfg) is None

    def test_no_candidates_returns_none(self, tmp_path):
        cfg = _make_wordfill_cfg(tmp_path)
        assert desk.find_wordfill_match('run', 'en', cfg) is None

    def test_match_on_lemma(self, tmp_path):
        tsv = tmp_path / "20260710120000-session.en.tsv"
        _write_tsv(tsv, FULL_HEADERS,
                   [_row(quotation='running', lemma='run', inflected='running',
                         ipa='/rʌn/', morph='verb', dest='laufen')])
        cfg = _make_wordfill_cfg(tmp_path)
        result = desk.find_wordfill_match('run', 'en', cfg)
        assert result is not None
        assert result.get('WordSourceIPA') == '/rʌn/'

    def test_match_on_inflected_form(self, tmp_path):
        tsv = tmp_path / "20260710120000-session.en.tsv"
        _write_tsv(tsv, FULL_HEADERS,
                   [_row(quotation='running', lemma='run', inflected='running',
                         ipa='/rʌn/', morph='verb', dest='laufen')])
        cfg = _make_wordfill_cfg(tmp_path)
        result = desk.find_wordfill_match('running', 'en', cfg)
        assert result is not None
        assert result.get('WordSourceIPA') == '/rʌn/'

    def test_case_insensitive_match(self, tmp_path):
        tsv = tmp_path / "20260710120000-session.en.tsv"
        _write_tsv(tsv, FULL_HEADERS,
                   [_row(quotation='Apple', lemma='Apple', inflected='Apple',
                         ipa='/ˈæpəl/', morph='noun', dest='Apfel')])
        cfg = _make_wordfill_cfg(tmp_path)
        result = desk.find_wordfill_match('apple', 'en', cfg)
        assert result is not None
        assert result.get('WordSourceIPA') == '/ˈæpəl/'

    def test_newer_zid_wins_over_older_regardless_of_quality(self, tmp_path):
        """Newer file (bare quality) should win over older file (full quality)."""
        old_tsv = tmp_path / "20260701120000-old.en.tsv"
        new_tsv = tmp_path / "20260710120000-new.en.tsv"
        _write_tsv(old_tsv, FULL_HEADERS,
                   [_row(lemma='run', ipa='/rʌn/', morph='verb')])
        _write_tsv(new_tsv, FULL_HEADERS,
                   [_row(lemma='run', ipa='', morph='')])
        cfg = _make_wordfill_cfg(tmp_path)
        result = desk.find_wordfill_match('run', 'en', cfg)
        # Newer file wins — no IPA in result because new file has bare quality
        assert result is None or result.get('WordSourceIPA', '') == ''

    def test_quality_tier_wins_within_same_zid_prefix(self, tmp_path):
        """Within the same file, the row with higher quality (IPA+morph) is chosen."""
        tsv = tmp_path / "20260710120000-session.en.tsv"
        _write_tsv(tsv, FULL_HEADERS, [
            _row(lemma='run', ipa='', morph=''),          # bare
            _row(lemma='run', ipa='/rʌn/', morph='verb'), # full
        ])
        cfg = _make_wordfill_cfg(tmp_path)
        result = desk.find_wordfill_match('run', 'en', cfg)
        assert result is not None
        assert result.get('WordSourceIPA') == '/rʌn/'

    def test_min_quality_full_rejects_partial_when_strict(self, tmp_path):
        """min_quality='full' with effort='strict' must reject a row with only IPA filled."""
        tsv = tmp_path / "20260710120000-session.en.tsv"
        _write_tsv(tsv, FULL_HEADERS,
                   [_row(lemma='run', ipa='/rʌn/', morph='')])
        cfg = _make_wordfill_cfg(tmp_path, min_quality='full', effort='strict')
        result = desk.find_wordfill_match('run', 'en', cfg)
        assert result is None

    def test_min_quality_partial_accepts_partial(self, tmp_path):
        """min_quality='partial' must accept a row with only IPA filled."""
        tsv = tmp_path / "20260710120000-session.en.tsv"
        _write_tsv(tsv, FULL_HEADERS,
                   [_row(lemma='run', ipa='/rʌn/', morph='')])
        cfg = _make_wordfill_cfg(tmp_path, min_quality='partial')
        result = desk.find_wordfill_match('run', 'en', cfg)
        assert result is not None
        assert result.get('WordSourceIPA') == '/rʌn/'

    def test_corrupt_file_is_skipped(self, tmp_path, caplog):
        """A corrupt/unreadable TSV file must be skipped with a warning."""
        corrupt = tmp_path / "20260710120000-corrupt.en.tsv"
        corrupt.write_bytes(b'\xff\xfe invalid utf-8 \x80\x81')
        good = tmp_path / "20260709120000-good.en.tsv"
        _write_tsv(good, FULL_HEADERS,
                   [_row(lemma='run', ipa='/rʌn/', morph='verb')])
        cfg = _make_wordfill_cfg(tmp_path)
        import logging
        with caplog.at_level(logging.WARNING, logger='root'):
            result = desk.find_wordfill_match('run', 'en', cfg)
        # Should still find result from the good file
        assert result is not None
        # Warning should mention the corrupt file
        assert any("skipping" in rec.message.lower() or "corrupt" in rec.message.lower()
                   or "wordfill" in rec.message.lower()
                   for rec in caplog.records)

    def test_only_non_empty_eligible_fields_in_result(self, tmp_path):
        """Result dict must only contain non-empty eligible field values."""
        tsv = tmp_path / "20260710120000-session.en.tsv"
        _write_tsv(tsv, FULL_HEADERS,
                   [_row(lemma='run', ipa='/rʌn/', morph='verb', dest='')])
        cfg = _make_wordfill_cfg(tmp_path)
        result = desk.find_wordfill_match('run', 'en', cfg)
        assert result is not None
        # Empty dest must not appear
        assert 'WordDestination' not in result
        # Non-empty ipa and morph must appear
        assert result.get('WordSourceIPA') == '/rʌn/'
        assert result.get('WordSourceMorphologyAI') == 'verb'


# ---------------------------------------------------------------------------
# 5.4  apply_wordfill_to_rows
# ---------------------------------------------------------------------------

class TestApplyWordfillToRows:

    BASE_HEADERS = ['WordSource', 'WordSourceIPA', 'WordSourceMorphologyAI', 'WordDestination']

    def test_empty_cells_are_filled(self):
        rows = [['run', '', '', '']]
        match = {'WordSourceIPA': '/rʌn/', 'WordSourceMorphologyAI': 'verb'}
        desk.apply_wordfill_to_rows(rows, self.BASE_HEADERS, match)
        assert rows[0][1] == '/rʌn/'
        assert rows[0][2] == 'verb'

    def test_non_empty_cells_are_not_overwritten(self):
        rows = [['run', '/existing/', 'noun', '']]
        match = {'WordSourceIPA': '/rʌn/', 'WordSourceMorphologyAI': 'verb'}
        desk.apply_wordfill_to_rows(rows, self.BASE_HEADERS, match)
        assert rows[0][1] == '/existing/'
        assert rows[0][2] == 'noun'

    def test_unrecognized_columns_in_match_are_ignored(self):
        rows = [['run', '', '', '']]
        match = {'WordSourceIPA': '/rʌn/', 'UnknownColumn': 'should_be_ignored'}
        desk.apply_wordfill_to_rows(rows, self.BASE_HEADERS, match)
        assert rows[0][1] == '/rʌn/'
        # No error and no extra column added
        assert len(rows[0]) == len(self.BASE_HEADERS)

    def test_columns_not_in_headers_are_ignored(self):
        rows = [['run', '', '']]
        headers = ['WordSource', 'WordSourceIPA', 'WordDestination']
        match = {'WordSourceMorphologyAI': 'verb'}  # not in headers
        desk.apply_wordfill_to_rows(rows, headers, match)
        # No error, row unchanged
        assert rows[0] == ['run', '', '']

    def test_short_row_is_extended(self):
        """Rows shorter than the header length should be extended before filling."""
        rows = [['run']]  # missing WordSourceIPA, WordSourceMorphologyAI, WordDestination
        match = {'WordSourceIPA': '/rʌn/'}
        desk.apply_wordfill_to_rows(rows, self.BASE_HEADERS, match)
        ipa_idx = self.BASE_HEADERS.index('WordSourceIPA')
        assert rows[0][ipa_idx] == '/rʌn/'

    def test_multiple_rows_all_filled(self):
        rows = [['run', '', '', ''], ['run', '', '', 'existing']]
        match = {'WordSourceIPA': '/rʌn/', 'WordDestination': 'laufen'}
        desk.apply_wordfill_to_rows(rows, self.BASE_HEADERS, match)
        assert rows[0][1] == '/rʌn/'
        assert rows[0][3] == 'laufen'
        assert rows[1][1] == '/rʌn/'
        # row[1] already had 'existing' destination — must not be overwritten
        assert rows[1][3] == 'existing'

    def test_only_eligible_fields_are_applied(self):
        """Even if match contains a non-eligible key, it must not be applied."""
        rows = [['run', '', '', '']]
        match = {
            'WordSourceIPA': '/rʌn/',
            'SentenceSource': 'I like to run',  # not eligible
        }
        desk.apply_wordfill_to_rows(rows, self.BASE_HEADERS + ['SentenceSource'], match)
        ipa_idx = self.BASE_HEADERS.index('WordSourceIPA')
        sentence_idx = (self.BASE_HEADERS + ['SentenceSource']).index('SentenceSource')
        assert rows[0][ipa_idx] == '/rʌn/'
        # SentenceSource must not be filled (not in WORDFILL_ELIGIBLE_FIELDS)
        assert len(rows[0]) <= sentence_idx or rows[0][sentence_idx] == ''


# ---------------------------------------------------------------------------
# Integration tests with run_lookup_flow
# ---------------------------------------------------------------------------

import subprocess

class TestWordfillIntegration:

    def test_run_lookup_flow_prefills_and_bypasses_translation(self, monkeypatch, tmp_path):
        """
        run_lookup_flow with wordfill enabled should fill translations and bypass
        calling the slow translation API for matched words, and it should preserve
        the filled IPA/morphology even if run_intellifiller=False.
        """
        # Setup lookup test env (borrow setup_test_env from test_lookup)
        from tests.test_lookup import setup_test_env
        config, resolved_paths, goldendict, _wf = setup_test_env(tmp_path)

        # Set run_intellifiller = False
        goldendict['run_intellifiller'] = False
        
        # Configure wordfill
        wordfill_cfg = {
            'enabled': True,
            'scan_roots': [tmp_path],
            'search_depth': 0,
            'data_mode': 'all',
            'min_quality': 'any',
            'max_scan_files': 500,
            'language_strict': True,
        }

        # Mock find_wordfill_match to return a full match for 'test'
        match = {
            'WordDestination': 'тест_wordfilled',
            'WordSourceIPA': '/tɛst/',
            'WordSourceMorphologyAI': 'noun',
        }
        monkeypatch.setattr(desk, 'find_wordfill_match', lambda w, l, cfg, exclude_path=None: match if w == 'test' else None)

        # Mock translate_source_text for sentences
        monkeypatch.setattr(desk, 'translate_source_text', lambda *a, **kw: {0: "working"})

        # Mock subprocess.run to provide initial TSV containing the word 'test'
        def mock_run(*args, **kwargs):
            cmd = args[0]
            out_file = Path(cmd[cmd.index("--output-file") + 1])
            out_file.parent.mkdir(parents=True, exist_ok=True)
            # headers must contain all fields
            headers = [
                'WordSource', 'WordDestination', 'WordSourceIPA', 'WordSourceMorphologyAI',
                'SentenceSourceIndex', 'SentenceSourceContextLeft', 'SentenceSource', 'SentenceSourceContextRight',
                'SentenceDestinationContextLeft', 'SentenceDestination', 'SentenceDestinationContextRight',
                'SentenceDestination2ContextLeft', 'SentenceDestination2', 'SentenceDestination2ContextRight',
                'SentenceSourceWordlist', 'SentenceSourceCloze', 'SentenceSourceRewriteAISentenceSource',
                'SentenceSourceRewriteAISentenceDestination'
            ]
            rows = [['test', '', '', '', '0', '', 'test context', '', '', '', '', '', '', '', '', '', '', '']]
            
            with open(out_file, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f, delimiter='\t', lineterminator='\n')
                writer.writerow(headers)
                for row in rows:
                    writer.writerow(row)

        monkeypatch.setattr(subprocess, 'run', mock_run)

        # Mock translate_lemmas_fast_path — we expect it NOT to be called or to receive no lemmas
        # because the word 'test' translation is already filled by wordfill!
        translate_lemmas_called = []
        def mock_translate_lemmas_fast_path(lemmas, *args, **kwargs):
            translate_lemmas_called.append(lemmas)
            return {l: 'fallback_transl' for l in lemmas}
        monkeypatch.setattr(desk, 'translate_lemmas_fast_path', mock_translate_lemmas_fast_path)

        # Run lookup flow
        comments, headers, data_rows, sent_trans = desk.run_lookup_flow(
            text="test context",
            language="en",
            target_lang="ru",
            fmt="text",
            config=config,
            resolved_paths=resolved_paths,
            goldendict=goldendict,
            zid="test_zid",
            text_mode="single",
            wordfill_cfg=wordfill_cfg
        )

        # Verify translate_lemmas_fast_path was NOT called with 'test' (because it was pre-filled)
        assert len(translate_lemmas_called) == 0 or 'test' not in translate_lemmas_called[0]

        # Verify data_rows has the filled translation, IPA and morphology (and they were NOT cleared)
        lemma_idx = headers.index('WordSource')
        dest_idx = headers.index('WordDestination')
        ipa_idx = headers.index('WordSourceIPA')
        morph_idx = headers.index('WordSourceMorphologyAI')

        row_test = [r for r in data_rows if r[lemma_idx] == 'test'][0]
        assert row_test[dest_idx] == 'тест_wordfilled'
        assert row_test[ipa_idx] == '/tɛst/'
        assert row_test[morph_idx] == 'noun'
