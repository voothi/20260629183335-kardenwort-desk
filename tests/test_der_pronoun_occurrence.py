import configparser
import pytest
import text_tokenizer as tok
from kardenwort_desk import deduplicate_rows, normalize_pos_tag


def test_deduplicate_rows_combines_der_article_and_pronoun():
    """Verify that when der tokens have both art. and pron. in a sentence,
    deduplicate_rows produces a merged POS tag containing both art. and pron."""
    rows = [
        ["der", "der", "art.", "тот", "9"],
        ["der", "die", "pron.", "который", "16"],
        ["der", "das", "art.", "тот", "25"],
        ["der", "den", "art.", "тот", "29"],
        ["der", "der", "pron.", "который", "38"],
    ]
    cfg = configparser.ConfigParser()
    cfg.add_section("Settings")
    cfg.set("Settings", "unify_article_pronoun_lemmas", "true")
    cfg.set("Settings", "combine_source_words", "true")
    cfg.set("Settings", "filter_inflected_by_window", "false")
    deduped = deduplicate_rows(rows, col_word_source=0, col_pos=2, col_inflected=1, config=cfg)
    assert len(deduped) == 1
    combined_pos = deduped[0][2]
    assert "art." in combined_pos
    assert "pron." in combined_pos


def test_der_auto_infer_occurrences_relative_clause():
    """Verify that auto-infer correctly distinguishes relative pronoun tokens from articles."""
    sent = "Er fängt heute mit der Arbeit an, die ihm gefällt, weil das Projekt den Erfolg bringen soll, der ihm versprochen wurde."
    source_tokens = tok.build_word_list_internal(sent, keep_spaces=True)
    der_forms = {"der", "die", "das", "den", "dem", "des"}

    inferred_occ = {}
    for tok_item in source_tokens:
        if tok_item.get("is_word") and (tok_item.get("lower_clean") or "").lower() in der_forms:
            v_key = str(tok_item.get("visual_idx"))
            v_int = tok_item.get("visual_idx", 0)
            prev_toks = [st for st in source_tokens if st.get("visual_idx", 0) < v_int and st.get("text", "").strip()]
            prev_t = prev_toks[-1].get("text", "").strip() if prev_toks else ""
            is_rel_pron = (prev_t == "," or prev_t.endswith(","))
            pos_inferred = "pron." if is_rel_pron else "art."
            trans_inferred = "который" if pos_inferred == "pron." else "тот"
            inferred_occ[v_key] = {"pos": pos_inferred, "trans": trans_inferred}

    # Visual 9: mit der Arbeit -> art.
    assert inferred_occ["9"]["pos"] == "art."
    # Visual 16: , die ihm gefällt -> pron.
    assert inferred_occ["16"]["pos"] == "pron."
    # Visual 25: weil das Projekt -> art.
    assert inferred_occ["25"]["pos"] == "art."
    # Visual 29: den Erfolg -> art.
    assert inferred_occ["29"]["pos"] == "art."
    # Visual 38: , der ihm versprochen wurde -> pron.
    assert inferred_occ["38"]["pos"] == "pron."
