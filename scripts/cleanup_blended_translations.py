"""
Cleanup script for single-occurrence blended translation rows in SQLite database.
Restores word_destination from extra_fields.ru when word_destination was blended
with comma joins and the sentence only contained a single occurrence of the lemma.

Default mode is dry-run. Pass --apply to perform database updates.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import text_tokenizer as tok
from kardenwort_db import sanitize_extra_fields


def cleanup_blended_translations(db_path: Path, apply_changes: bool = False):
    if not db_path.exists():
        print(f"Error: Database file not found: {db_path}", file=sys.stderr)
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Find words with comma-blended word_destination
    cursor.execute("""
        SELECT w.id, w.session_zid, w.sentence_index, w.lemma, w.quotation,
               w.word_destination, w.extra_fields,
               s.sentence_source
        FROM words w
        LEFT JOIN sentences s
          ON w.session_zid = s.session_zid AND w.sentence_index = s.sentence_index
        WHERE w.word_destination LIKE '%, %'
          AND w.extra_fields IS NOT NULL
          AND length(trim(w.extra_fields)) > 0
    """)
    candidates = cursor.fetchall()

    affected_rows = []

    for row in candidates:
        w_id = row["id"]
        sess_zid = row["session_zid"]
        sent_idx = row["sentence_index"]
        lemma = (row["lemma"] or "").strip()
        quotation = (row["quotation"] or "").strip()
        w_dest = (row["word_destination"] or "").strip()
        ef_raw = row["extra_fields"]
        sent_source = (row["sentence_source"] or "").strip()

        try:
            ef = sanitize_extra_fields(ef_raw)
        except Exception:
            continue

        clean_ru = (ef.get("ru") or "").strip()
        if not clean_ru or ", " in clean_ru:
            continue

        # If sentence_source is missing from sentences table, try reconstructing from other words or sessions
        if not sent_source:
            cursor.execute(
                "SELECT sentence_source FROM sentences WHERE session_zid = ? AND sentence_index = ? LIMIT 1",
                (sess_zid, sent_idx)
            )
            s_row = cursor.fetchone()
            if s_row and s_row["sentence_source"]:
                sent_source = s_row["sentence_source"].strip()

        # Check lemma occurrence count in sentence (fail-safe: require exactly 1 match)
        is_single_occurrence = False
        if sent_source and lemma:
            try:
                tokens = tok.build_word_list_internal(sent_source, keep_spaces=True)
                w_lem = lemma.lower()
                quots = [q.strip().lower() for q in quotation.split(",") if q.strip()]
                match_count = sum(
                    1 for t in tokens
                    if t.get("is_word") and (
                        (t.get("lower_clean") or t.get("text") or "").strip().lower() == w_lem
                        or (t.get("lower_clean") or t.get("text") or "").strip().lower() in quots
                    )
                )
                if match_count == 1:
                    is_single_occurrence = True
            except Exception:
                is_single_occurrence = False

        if not is_single_occurrence:
            continue

        affected_rows.append({
            "id": w_id,
            "session_zid": sess_zid,
            "sentence_index": sent_idx,
            "lemma": lemma,
            "old_destination": w_dest,
            "new_destination": clean_ru,
            "extra_fields": ef,
        })

    mode_str = "APPLY" if apply_changes else "DRY-RUN"
    print(f"[{mode_str}] Found {len(affected_rows)} candidate rows for cleanup in {db_path}:")

    for item in affected_rows:
        print(
            f"  ID {item['id']} (session={item['session_zid']}, sent={item['sentence_index']}, lemma='{item['lemma']}'):\n"
            f"    '{item['old_destination']}' -> '{item['new_destination']}'"
        )

    if apply_changes and affected_rows:
        for item in affected_rows:
            ef = item["extra_fields"]
            occ_data = ef.get("occurrence_data", {})
            if isinstance(occ_data, dict) and occ_data:
                # Update any occurrences with the clean destination
                for k, v in occ_data.items():
                    if isinstance(v, dict) and v.get("trans") == item["old_destination"]:
                        v["trans"] = item["new_destination"]
                ef["occurrence_data"] = occ_data

            cursor.execute(
                "UPDATE words SET word_destination = ?, extra_fields = ? WHERE id = ?",
                (item["new_destination"], json.dumps(ef, ensure_ascii=False), item["id"])
            )
        conn.commit()
        print(f"\nSuccessfully updated {len(affected_rows)} rows in {db_path}.")
    elif not apply_changes:
        print(f"\nDry-run completed. Run with --apply to commit {len(affected_rows)} updates.")

    conn.close()
    return affected_rows


def main():
    parser = argparse.ArgumentParser(description="Clean up blended word_destination values.")
    parser.add_argument("--db", default="data/kardenwort.db", help="Path to SQLite database")
    parser.add_argument("--apply", action="store_true", help="Apply updates to database (default is dry-run)")
    args = parser.parse_args()

    db_path = Path(args.db)
    cleanup_blended_translations(db_path, apply_changes=args.apply)


if __name__ == "__main__":
    main()
