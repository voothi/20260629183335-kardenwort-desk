/// <reference path="app-contracts.d.ts" />
/** Shared script APIs installed by the canonical browser/native host. */
interface Window {
    AppStore: typeof import('./state/store');
    SSEBridge: typeof import('./services/sse');
    WorkspaceTabs: typeof import('./components/WorkspaceTabs');
    addClass(element: Element | null, name: string): void;
    removeClass(element: Element | null, name: string): void;
    addEvent<K extends keyof HTMLElementEventMap>(target: EventTarget, type: K, listener: (this: HTMLElement, event: HTMLElementEventMap[K]) => void): void;
    ahkCall?: (action: string, payload: string) => string;
    __deskInstanceId?: number;
    getDeltas(): string;
    isDirty(): boolean;
    applySharedSelection(membership: Record<string, boolean>): void;
    startSelectionBridge(): void;
    stopSelectionBridge(): void;
    abortSaveSnapshot(): void;
    captureSaveSnapshot(): string;
    acknowledgeSaveSnapshot(serialized: string, response?: string): boolean;
    receiveUpdate(payload: import('./types').UpdatePayload): void;
    forceRepaint(): number;
    tokenizeSource(): void;
    rebindTokenSpans(): void;
    buildLcIndex(): void;
    updateBidirectionalHighlights(): void;
}
