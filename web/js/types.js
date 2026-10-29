
/**
 * @file types.js
 * Canonical descriptive JSDoc for shared client records and coordinate meanings.
 * Documentation only: runtime boundaries validate inputs; no compiler or build step is required.
 */

/**
 * @typedef {Object} Token
 * @property {number} [visual_idx] - 0-based visual presentation index in text stream
 * @property {string} word - Surface text token
 * @property {string} [lemma] - Normalized dictionary lemma
 * @property {string|number} [row_id] - Associated vocabulary table row ID
 * @property {number} [token_order] - Strict sequential token ordering
 * @property {number} [sentence_idx] - Associated sentence card sequence index
 * @property {boolean} [is_word] - Flag indicating word vs punctuation/whitespace
 * @property {string} [lower_clean] - Lowercased sanitized token string
 * @property {Array<string|number>} [atomic_row_ids] - Linked row IDs for compound tokens
 * @property {Array<string|number>} [row_ids] - Array of all linked row IDs
 * @property {number|string|null} [atomic_id] - Occurrence identity; never a sentence-local offset
 * @property {Array<string|number>} [all_row_ids] - Row identities represented by an aggregate token
 * @property {Array<string|number>} [compound_row_ids] - Rows represented by a compound token
 * @property {string} [text] - Original text when supplied separately from the surface word
 * @property {string} [pos] - Part-of-speech label used by token interaction
 * @property {string} [highlight_class] - Active highlight CSS class name
 * @property {string} [mvp_type] - 'source' | 'trans'
 * @property {number} [mvp_idx] - Index within MVP bookmarking stream
 */

/**
 * @typedef {Object} OccurrenceTarget
 * @property {number} sentence_idx - Positive persistence sentence index
 * @property {number} token_order - Nonnegative token order within that sentence
 */

/**
 * @typedef {Object} VocabularyRow
 * @property {string|number} row_id - Unique table row identifier
 * @property {number} [token_order] - Ordering coordinate within sentence token stream
 * @property {number} [sentence_idx] - Parent sentence index
 * @property {string} [inflected] - Inflected surface word form
 * @property {string} lemma - Base dictionary lemma
 * @property {string} [pos] - Part of speech tag
 * @property {string} [gender] - Grammatical gender
 * @property {string} [translation] - Target language translation
 * @property {boolean} [is_pending] - Whether translation is currently pending
 * @property {string|null} [pending_provider] - Provider identifier actively being polled
 * @property {string} [ipa] - International Phonetic Alphabet pronunciation
 * @property {string} [morphology] - Morphological feature analysis
 * @property {string} [provenance] - Origin provider/model identifier
 * @property {boolean|number|string} [selected] - Row selection state
 * @property {string|null} [row_html] - Pre-rendered cached HTML representation
 * @property {boolean} [is_dirty] - Row dirty edit flag
 * @property {string} [word]
 * @property {string} [highlight_class]
 * @property {Array<string|number>} [all_row_ids]
 * @property {string} [sentence_text]
 * @property {string} [inflected_tooltip]
 * @property {Object} [occurrence_data]
 * @property {Array<OccurrenceTarget>} [occurrence_targets] - Exact underlying persistence identities
 * @property {string} [dynamic_tds]
 */

/**
 * @typedef {Object} ProvenanceRecord
 * @property {string} [source] - Provider name (e.g., 'google', 'deepl', 'argos')
 * @property {string} [engine] - Translation engine or dictionary service
 * @property {string} [model] - Underlying model identifier
 * @property {string|number} [timestamp] - Generation timestamp
 * @property {number} [confidence] - Confidence score between 0.0 and 1.0
 * @property {boolean} [cached] - Whether result was resolved from cache
 * @property {string} [label] - Human-readable provider label
 */

/**
 * @typedef {Object} UIConfig
 * @property {boolean} [highlight_same_lemma] - Highlight all matching lemma tokens across containers
 * @property {boolean} [filter_selected_only] - Restrict table view to selected rows
 * @property {string} [theme] - 'dark' | 'light' | 'compact'
 * @property {boolean} [audio_rmb_play] - Play audio on right-click
 * @property {string} [audio_rmb_source] - 'lemma' | 'word_translation'
 * @property {string} [audio_rmb_chain_mode] - 'separate' | 'joined'
 * @property {boolean} [audio_lmb_play] - Play audio on left-click
 * @property {string} [audio_lmb_source] - 'lemma' | 'word_translation'
 * @property {string} [audio_lmb_chain_mode] - 'separate' | 'joined'
 * @property {string} [audio_source_range_mode] - 'all' | 'sentence'
 * @property {string} [audio_table_range_mode] - 'none' | 'row'
 * @property {number} [zoom_scale] - UI scale factor
 * @property {boolean} [auto_save_on_edit] - Cell-edit autosave; selection persistence is independent
 * @property {boolean} [auto_save_on_close] - Persist current pending edits when closing
 * @property {Object} [grammar] - Shared grammar labels and classification tables
 * @property {Object<string,number>} [pos_display_order] - Configured part-of-speech order
 */

/**
 * @typedef {Object} UpdatePayload
 * @property {string} [stage] - Current pipeline stage ('init' | 'tokenized' | 'translated_text' | 'finished' | 'error')
 * @property {string} [status] - 'ok' | 'processing' | 'finished' | 'error'
 * @property {boolean} [is_finished] - Terminal update flag
 * @property {boolean} [is_global] - Update spans all sentence cards
 * @property {boolean} [all_sentences] - Legacy global-update flag
 * @property {boolean} [ok] - Success flag
 * @property {UpdatePayload} [data] - Wrapped payload data
 * @property {string} [translated_text] - Updated sentence translation string
 * @property {string} [sentence_translation] - Alias for translated_text
 * @property {string} [translatedText] - Alias for translated_text
 * @property {string} [source_text] - Original source text
 * @property {string} [sourceText] - Alias for source_text
 * @property {string} [text_provenance] - Source translation provenance label
 * @property {string} [textProvenance] - Alias for text_provenance
 * @property {Object<string, VocabularyRow>} [rows] - Delta row updates keyed by token_order or row_id
 * @property {Array<WorkspaceCard>} [sentence_cards] - Multi-sentence workspace cards
 * @property {Array<any>} [sentences] - Updated sentence strings
 * @property {string} [session_zid] - Active session ZID
 * @property {string} [error] - Error message if stage is 'error'
 * @property {Object<string,boolean>} [shared_selection] - Authoritative normalized lemma membership
 * @property {Object<string,string>} [row_provenances] - Per-occurrence provenance labels
 * @property {Object<string,string>} [rowProvenances] - Legacy ingress alias for row_provenances
 */

/**
 * @typedef {Object} SessionState
 * @property {string} [zid] - Current session ZID identifier
 * @property {string} [session_zid] - Current session ZID identifier
 * @property {string} [language] - Source language code (e.g. 'de')
 * @property {string} [target_lang] - Target language code (e.g. 'ru')
 * @property {string} [target_language] - Target language alias
 * @property {string} [source_text] - Raw source text
 * @property {string} [sourceText] - Legacy source projection alias
 * @property {string} [translated_text] - Full translated text
 * @property {string|null} [translatedText] - Live translation projection; null means no replacement
 * @property {string} [sentence_translation] - Translated text alias
 * @property {string} [text_mode] - 'single' | 'multi'; independent of window delivery
 * @property {string} [delivery_mode] - 'container' | 'multi_window' (legacy host alias: 'tabs')
 * @property {boolean} [server_enabled] - Whether background API server is connected
 * @property {string} [server_url] - HTTP API server endpoint base URL
 * @property {string} [api_token] - Authentication bearer token
 * @property {string} [fingerprint] - Content integrity hash
 * @property {Array<string>} [headers] - Vocabulary table column headers
 * @property {Array<Array<any>>} [data_rows] - Raw matrix rows
 * @property {Array<WorkspaceCard>} [sentence_cards] - Sentence card array
 * @property {UIConfig} [config] - UI configuration settings
 * @property {UIConfig} [ui_config] - UI configuration settings alias
 * @property {Object} [bootstrap] - Bootstrap session data payload
 * @property {Object<string,VocabularyRow>} [rows] - Live row projections keyed by occurrence identity
 * @property {Object<string, boolean>} [selectedLemmas] - Set of active lemma selections
 * @property {boolean} [highlightSameLemma] - Same-lemma highlighting active
 * @property {boolean} [filterSelectedOnly] - Filter selected rows active
 * @property {boolean} [isFinished] - All processing stages complete
 * @property {Array<number|string>} [selectedRowIds] - Selected row identifiers
 * @property {Array<number|string>} [selected_ids] - Selected row identifiers legacy alias
 * @property {Array<Token>} [activeTokenSelections] - Active word selections
 * @property {number|null} [activeSentenceIdx] - Active sentence index
 * @property {number|null} [activeTabSeq] - Active workspace tab sequence number
 * @property {function(any): void} [applyDeltas] - Legacy delta merger attached by app.js
 * @property {string|null} [stage] - Current processing stage
 * @property {string|null} [textProvenance] - Provenance of the visible text translation
 * @property {string|null} [textTranslationStatus] - Current text translation status
 * @property {boolean} [textTranslationFailed] - Terminal text translation failure
 * @property {Array<WorkspaceCard>} [sentenceCards] - Legacy ingress alias for sentence_cards
 */

/**
 * A persisted edit addresses one occurrence. sentence_idx is the stored sentence
 * coordinate (including overview 0), while token_order is the stored token coordinate.
 * @typedef {Object} Delta
 * @property {number|string} row_id - UI row identity
 * @property {number|string} token_order - Stored token coordinate
 * @property {number} sentence_idx - Stored sentence coordinate
 * @property {string} column - Persisted field or deletion command
 * @property {string|boolean} value - Submitted value
 * @property {Object} [restore] - Exact deleted-record restoration data
 */

/**
 * Edit actions carry rowId/tokenOrder/sentenceIdx, column, oldValue/newValue and cell.
 * Delete actions carry matching rowIds/tokenOrders/sentenceIndices arrays.
 * History records retain user intent independently of save acknowledgments.
 * @typedef {Object} HistoryAction
 * @property {'edit'|'delete'} type - Action discriminator
 * @property {number} [rowId]
 * @property {number} [tokenOrder]
 * @property {number} [sentenceIdx]
 * @property {string} [column]
 * @property {any} [oldValue]
 * @property {any} [newValue]
 * @property {HTMLElement} [cell]
 * @property {Array<number>} [rowIds]
 * @property {Array<number>} [tokenOrders]
 * @property {Array<number>} [sentenceIndices]
 */

/**
 * @typedef {Object} SaveState
 * @property {'idle'|'saving'|'reconciling'|'reconcile_failed'} phase - Save coordinator phase
 * @property {Promise|null} promise - Current serialized persistence/reconciliation work
 * @property {Array<Delta>} [snapshot] - Submitted values while persistence is in flight
 */

/**
 * @typedef {Object} SaveResponse
 * @property {Array<Delta>} [deleted_rows] - Restorable records acknowledged by persistence
 */

/**
 * @typedef {Object} ErrorResponse
 * @property {string} [message] - User-facing failure detail
 * @property {string} [error] - Compatible failure message alias
 * @property {string} [error_trace] - Diagnostic detail
 * @property {string} [trace_id] - Correlation identity
 * @property {ErrorResponse} [data] - Optional response envelope
 */

/**
 * @typedef {Object} ChildSession
 * @property {string} [zid] - Session identity
 * @property {string} [session_zid] - Compatible session identity alias
 * @property {number|string} [seq_num] - Native child window sequence
 */

/**
 * @typedef {Object} WorkspaceCard
 * @property {number} seq_num
 * @property {number} sentence_idx
 * @property {Array<VocabularyRow>} [words]
 * @property {string} [translated_text]
 * @property {string} [source_text] - Card source sentence text
 * @property {Array<string>} [selected_ids]
 * @property {string} [tsv_filename]
 * @property {string} [zid]
 * @property {number} [index]
 * @property {string} [label]
 */

/**
 * @typedef {Object} TabNavigationState
 * @property {number|null} activeTabSeq
 * @property {number} activeSentenceIdx
 * @property {Array<WorkspaceCard>} cards
 * @property {string} masterSourceHtml
 */

/**
 * @typedef {Object} SentenceSyncItem
 * @property {number} [sentence_index]
 * @property {string} [sentence_destination]
 * @property {string} [translation]
 */

/**
 * @typedef {Array<SentenceSyncItem|string> | Object<string, string|SentenceSyncItem>} SentenceSyncPayload
 */

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {};
}
