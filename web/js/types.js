// @ts-check

/**
 * @file types.js
 * Comprehensive normative JSDoc type definitions for Kardenwort client domain entities.
 * Compile-free static type safety without external build tools.
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
 * @property {string} [highlight_class] - Active highlight CSS class name
 * @property {string} [mvp_type] - 'source' | 'trans'
 * @property {number} [mvp_idx] - Index within MVP bookmarking stream
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
 * @property {Object<string, any>} [rows] - Delta row updates keyed by token_order or row_id
 * @property {Array<any>} [sentence_cards] - Multi-sentence workspace cards
 * @property {Array<any>} [sentences] - Updated sentence strings
 * @property {string} [session_zid] - Active session ZID
 * @property {string} [error] - Error message if stage is 'error'
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
 * @property {string} [sentence_translation] - Translated text alias
 * @property {string} [text_mode] - 'single' | 'multi' | 'container'
 * @property {string} [delivery_mode] - 'container' | 'direct' | 'offline'
 * @property {boolean} [server_enabled] - Whether background API server is connected
 * @property {string} [server_url] - HTTP API server endpoint base URL
 * @property {string} [api_token] - Authentication bearer token
 * @property {string} [fingerprint] - Content integrity hash
 * @property {Array<string>} [headers] - Vocabulary table column headers
 * @property {Array<Array<any>>} [data_rows] - Raw matrix rows
 * @property {Array<any>} [sentence_cards] - Sentence card array
 * @property {UIConfig} [config] - UI configuration settings
 * @property {UIConfig} [ui_config] - UI configuration settings alias
 * @property {Object} [bootstrap] - Bootstrap session data payload
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
 */

if (typeof module !== 'undefined' && module.exports) {
    module.exports = {};
}
