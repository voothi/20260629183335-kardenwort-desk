/** Contracts for the shared browser/MSHTML script host. No runtime output. */
declare namespace Desk {
    type RowId = string | number;
    type Membership = Record<string, boolean>;
    interface Grammar {
        gender_full_name_map?: Record<string, string>;
        pos_full_name_map?: Record<string, string>;
        german_articles?: string[];
        german_pronouns?: string[];
        noun_tags?: string[];
        non_noun_tags?: string[];
        suffixes_masculine?: string[];
        suffixes_feminine?: string[];
        suffixes_neuter?: string[];
        suffix_excluded_ent?: string[];
        suffix_excluded_in?: string[];
    }
    type Config = import('./types').UIConfig & {
        grammar?: Grammar;
        zid?: string;
        pos_display_order?: Record<string, number>;
        has_highlight_col?: boolean|string;
        audio_anki_tts_cli?: string;
        audio_python_exe?: string;
        auto_save_on_close?: boolean;
        auto_save_on_edit?: boolean;
        compound_joiners?: string[];
    };
    type Word = import('./components/WorkspaceTabs').WordItem & {
        word?: string;
        all_row_ids?: RowId[];
        sentence_source?: string;
        sentence_translation?: string;
        word_translation?: string;
        token_indices?: number[];
    };
    interface Card {
        index?: number;
        sentence_idx: number;
        seq_num?: number;
        words?: Word[];
        translated_text?: string;
        source_text?: string;
    }
    type Token = Omit<import('./types').Token, 'word'|'sentence_idx'|'visual_idx'|'token_order'> & {
        word?: string;
        visual_idx?: RowId;
        token_order?: RowId;
        text?: string;
        pos?: string;
        compound_row_ids?: RowId[];
        atomic_id?: RowId|null;
        all_row_ids?: RowId[];
        sentence_idx?: number | null;
    };
    interface Bookmark {
        idx: string;
        srcSpan?: HTMLElement;
        transSpan?: HTMLElement;
        slot: number;
    }
    interface RowUpdate {
        row_id?: RowId;
        token_order?: RowId;
        lemma?: string;
        inflected?: string;
        inflected_tooltip?: string;
        sentence_source?: string;
        SentenceSource?: string;
        WordSource?: string;
        WordDestination?: string;
        trans?: string;
        pos?: string;
        WordSourcePOS?: string;
        WordSourcePos?: string;
        gender?: string;
        WordSourceGender?: string;
        morph?: string;
        WordSourceMorphologyAI?: string;
        classifications?: Record<string,string>;
        _provenance?: string;
        transProvenance?: string;
        translation?: string;
        word_translation?: string;
        ipa?: string;
        morphology?: string;
        provenance?: string;
        selected?: boolean | number | string;
    }
    type AppState = Omit<import('./types').SessionState, 'activeTokenSelections'|'bootstrap'|'sourceText'> & {
        activeTokenSelections: Token[];
        selectedLemmas: Membership;
        rows: Record<string, RowUpdate>;
        config?: Config;
        bootstrap?: {ui_config?: Config; session_data?: import('./types').SessionState; headers?: string[]; delivery_mode?: string};
        sentenceCards?: Card[];
        sentence_cards?: Card[];
        session_data?: import('./types').SessionState;
        deliveryMode?: string;
        sourceText?: string|null;
        translatedText?: string|null;
        activeTextProvider?: string;
        activeLemmaProvider?: string;
        activeProvider?: string;
        active_text_provider?: string;
        active_lemma_provider?: string;
        active_provider?: string;
        textProvenance?: string|null;
        text_provenance?: string;
        provenance?: string;
        stage?: string|null;
        status?: string|{isFinished?: boolean};
        lastError?: string;
        textTranslationFailed?: boolean;
        textTranslationStatus?: string|null;
        activeLemmas?: Membership;
    };
    interface Delta {
        row_id: RowId;
        token_order: RowId;
        sentence_idx: number;
        column: string;
        value: string | boolean;
        restore?: Record<string, unknown>;
    }
    interface EditAction {
        type: 'edit';
        rowId: number;
        tokenOrder: number;
        sentenceIdx: number;
        column: string;
        oldValue: string;
        newValue: string;
        cell: HTMLElement;
    }
    interface DeleteAction {
        type: 'delete';
        rowIds: RowId[];
        tokenOrders: RowId[];
        sentenceIndices: number[];
    }
    type HistoryAction = EditAction | DeleteAction;
    type IntentValue = Delta & {original: string | boolean; element?: Element | null};
    interface AcknowledgedValue {
        value: string | boolean;
        current: string | boolean;
        delta: Delta;
    }
    interface SaveResponse {deleted_rows?: Delta[]}
    interface LanguageMismatch {
        detected_language?: string;
        expected_language?: string;
        detected_name?: string;
        expected_name?: string;
        source_text?: string;
        session_zid?: string;
        text?: string;
    }
    interface ChildSession {zid?: string; session_zid?: string; seq_num?: string|number}
    interface CommandRequest {
        session_zid?: string; zid?: string; language?: string; token?: string;
        text?: string; theme?: string; text_mode?: string; bypass_lang_check?: boolean;
        row_ids?: RowId[]; selected_row_ids?: RowId[]; token_orders?: RowId[];
        sentence_idx?: number; deltas?: Delta[]; visual_indices?: RowId[];
        target_coordinates?: Array<{visual_idx?: RowId; sentence_idx?: number|null; token_order?: RowId; atomic_id?: RowId|null; lemma?: string}>;
        urls?: string[]; children?: ChildSession[];
    }
    type SaveState = {phase: "idle"; promise: null}|{phase: "saving"; promise: Promise<void>};
    interface ErrorResponse {message?: string; error?: string; error_trace?: string; trace_id?: string; data?: ErrorResponse}
}

interface Window {
    AppState: Desk.AppState;
    AppConfig?: {language?: string};
    __CONFIG__?: Desk.Config;
    SourceViewer: typeof import('./components/SourceViewer');
    TranslationView: typeof import('./components/TranslationView');
    LemmaTable: typeof import('./components/LemmaTable');
    AppView: {renderTranslatedText(text: string): boolean; renderRow(rowId: Desk.RowId, stage?: string): boolean};
    rebindTableRows(): void;
    API_TOKEN?: string;
    reserveTextRegions(reset?: boolean): void;
    escapeHtml(value: unknown): string;
    getSelectedRowIds(): number[];
    getSelectedRowsArray(extractTokenOrders?: boolean): Desk.RowId[];
    getSelectedRows(): string;
    kwToggleTag(checkbox: HTMLInputElement, rowId: Desk.RowId): void;
    toggleRowSelection(rowId: Desk.RowId, selected?: boolean): void;
    notifyAHKSelection(): void;
    kwExportSelected(): void;
    initSSE(sessionZid: string): void;
    sourceSpansArray: HTMLElement[];
    transSpansArray: HTMLElement[];
    mvpBookmarks: Desk.Bookmark[];
    __selectableTextMode: boolean;
    __persistentSelectableMode: boolean;
    __mvpInitialized?: boolean;
    __mvpBookmarks?: string;
    tokenizeText(text: string): string[];
    tokenizeTranslation(text?: string, words?: Desk.Word[]): void;
    formatProvenanceTooltip(provenance: unknown): string;
    wireMvpEvents(): void;
    toggleHighlightSameLemma(): void;
    toggleFilterSelectedOnly(): void;
    onSaveClick(quiet?: boolean): Promise<boolean>;
    onUpdateClick(forceReload?: boolean): Promise<void> | void;
    onRetextClick(): Promise<void> | void;
    onRewordClick(): Promise<void> | void;
    onSelectedClick(): Promise<void> | void;
    onSendToAnkiClick(): Promise<void> | void;
    onHandToolClick(): void;
    onDeleteClick(): void;
    onLangYes(): void;
    onLangNo(): void;
    onLangCancel(): void;
    wireActionToolbar(): void;
    playAudio(text: string, lang: string): void;
    undo(): void;
    redo(): void;
    triggerAutoSaveOnEdit(): void;
    sentence_cards?: Desk.Card[];
    hydrateSession(): void;
    ActiveXObject?: new (name: string) => unknown;
    formatTranslationCellTooltip(text: unknown, provenance?: unknown): string;
    toUnicodeBold(text: string): string;
    formatInflectedTooltip(sentence: string, inflected: string, order?: Desk.RowId, lemma?: string): string;
    formatLemmaTooltip(lemma: string, gender?: string, language?: string, pos?: string): string;
    formatPosTooltip(pos: string): string;
    formatGenderTooltip(gender: string, lemma?: string, provenance?: string): string;
    detectGermanGenderSuffix(lemma: string, gender: string): string | null;
    formatClassificationTooltip(value: string, role: string): string;
    formatGenderBadge(gender: string): string;
    formatProviderSkeletonLabel(provider: string): string;
    getTextBaseProvider(): string|undefined;
    getLemmaBaseProvider(): string|undefined;
    updateActiveProvider(task: string | null, provider: string): void;
    findTokenData(span: HTMLElement | null): Desk.Token | null;
    findTokenDataByVisualIdx(index: Desk.RowId): Desk.Token | null;
    findRelatedTokenSpans?(span: HTMLElement): HTMLElement[];
    getRowGroupIds(row: HTMLElement): string[];
    refreshBookmarkClasses(): void;
    getTargetIdx(index: number, isSource: boolean): number;
    isTokenPinnedInMVP(span: HTMLElement): boolean;
    unpinTokenFromMVP(span: HTMLElement): boolean;
    pinTokenToMVP(span: HTMLElement): boolean;
    clearMVPBookmarks(): boolean;
    getBookmarkIndices(): string;
    restoreBookmarksByIndices(serialized: string): void;
    setSelectableTextMode(active: boolean, persistent?: boolean): void;
    rebindMVPBookmarks(): void;
    POS_DISPLAY_ORDER_MAP: Record<string, number>;
    applyCardProgressiveUpdate(rows: Record<string, Desk.RowUpdate>, payload: import('./types').UpdatePayload): void;
    applyOccurrenceView(): void;
    applyOccurrenceRow(row: HTMLElement): void;
    _kwSentenceCards?: Desk.Card[];
    _kwActiveTabSeq?: number | null;
    getCardModels(): Desk.Card[]|null;
    countPendingSkeletons(): number;
    normalizeCardSkeletons(mode: string): void;
    _kwSkeletonPollTimer?: number | null;
    _kwEvtSource?: EventSource | null;
    _kwWatchdogResolved?: boolean;
    _kwPollSessionStatus?: () => void;
    _kwWatchdogMaxTimer?: number | null;
    _kwSseSafetyTimer?: number | null;
    _kwSseReconnectTimer?: number | null;
    _kwGetActiveElapsedMs?: () => number;
    _kwWatchdogMaxBudgetMs?: number;
    _kwAdvanceActiveElapsedMs(ms: number): void;
    _kwIsHeartbeat?: boolean;
    _kwEnterHeartbeatFallback(): void;
    _kwWatchdogOnVisibility(): void;
    _kwVisibilityListenerAttached?: boolean;
    _kwNetworkListenersAttached?: boolean;
    initWatchdog(): void;
    ensureWatchdogActive(reason: string): void;
    onSessionReload(): void;
    cleanupOrphanSkeletons(): void;
    startPolling(restart?: boolean): void;
    _kwStartWatchdogPolling(restart?: boolean): void;
    _kwConnectSse(reconnect?: boolean): void;
    showToast(message: string, type?: string, duration?: number, onClick?: (event: MouseEvent) => void): void;
    getRowLemma(row: HTMLElement): string;
    getLemmaByRowId(rowId: Desk.RowId): string;
    setLemmaSelected(lemma: string, selected: boolean): void;
    syncSelectionsToCardsAndState(): void;
    isCompoundDelimiterNode(node: Node | null): boolean;
    findCompoundSiblingSpans(span: HTMLElement): HTMLElement[];
    getWordPOS(span: HTMLElement): string;
    getExtraRowIds(row: HTMLElement): string[];
    isLegacyOverviewContext(): boolean;
    decideTokenClickAction(context: Record<string, boolean>): {shouldDeselect: boolean; isTableSelectedToken: boolean};
    isRowStillNeededByOtherActiveTokens(rowId: string, excluded: Desk.Membership): boolean;
    isLemmaStillNeededByOtherActiveTokens(lemma: string, excluded: Desk.Membership): boolean;
    pruneActiveTokensByRowOrLemma(rowId: Desk.RowId, lemma: string): void;
    isTokenInActiveSelections(index: number): boolean;
    clearTokenSelections(): void;
    addTokenToActiveSelections(span: HTMLElement): void;
    removeTokenFromActiveSelections(span: HTMLElement): void;
    saveActiveTokensToStorage(): void;
    restoreActiveTokensFromStorage(): void;
    findTableRowById(rowId: Desk.RowId): HTMLElement | null;
    getRawRowTranslations(span: HTMLElement): string[];
    getWordTranslation(span: HTMLElement): string;
    getWordLemma(span: HTMLElement): string;
    getWordInflectedForm(span: HTMLElement): string;
    __deskHandleMouseUp?: (event: MouseEvent) => void;
    clearAllSelectionsAndNotify(): void;
    clearAllSelections(): void;
    selectAllRows(): void;
    deleteSelectedRows(): void;
    getFocusedRowId(): Desk.RowId | null;
    updateEmptySelectionState(): void;
    getSelectedTokenOrdersArray(): Desk.RowId[];
    cancelActiveEdit: (() => void) | null;
    commitActiveEdit?: () => void;
    makeEditable(cell: HTMLElement): void;
    setSelectedRows(serialized: string): void;
    clearDirty(): void;
    editFocusedCell(): void;
    selectAllInActiveEdit(): void;
    copySelection(): void;
    updateToolbarState(): void;
    setButtonLoading(button: HTMLButtonElement | null, loading: boolean, text: string, fallback: string): void;
    formatErrorWithTrace(response: Desk.ErrorResponse|Error|string|null|undefined, fallback?: string): string;
    getApiToken(): string;
    retryRow(rowId: Desk.RowId, button: HTMLButtonElement): void;
    retrySession(sessionZid?: string): void;
    _kwRetryToastShown?: boolean;
    flushPendingSaves(): Promise<void>;
    showLanguageVerificationModal(info?: Desk.LanguageMismatch): void;
    __langMismatchInfo?: Desk.LanguageMismatch;
    hideLanguageVerificationModal(): void;
    setHighlightSameLemma(active: boolean): void;
    setFilterSelectedOnly(active: boolean): void;
    attachEvent?: (name: string, listener: EventListener) => void;
}
interface Document {documentMode?: number; selection?: {empty(): void}}
interface External {ahkCall?: (action: string, payload: string) => string}
interface HTMLElement {
    __kw_bound?: boolean;
    __kw_wired?: boolean;
    __kw_token_instance?: number;
    _recoveryTimer?: number|null;
}
interface Node {_origVal?: string}
interface WheelEvent {wheelDelta?: number}
interface HTMLElementEventMap {pagehide: PageTransitionEvent;mousewheel: WheelEvent; DOMMouseScroll: WheelEvent}
