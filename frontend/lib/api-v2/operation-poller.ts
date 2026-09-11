import type { WorkflowGateway } from "./gateway";
import type { OperationResponse } from "./types";

export type PollStopReason = "terminal" | "timeout" | "cancelled" | "request_error";

export interface PollStopEvent {
  reason: PollStopReason;
  operation: OperationResponse | null;
  error?: unknown;
}

export interface VisibilitySource {
  readonly hidden: boolean;
  addEventListener(type: "visibilitychange", listener: () => void): void;
  removeEventListener(type: "visibilitychange", listener: () => void): void;
}

export interface OperationPollerOptions {
  intervalMs?: number;
  maxAutoPollMs?: number;
  visibility?: VisibilitySource | null;
  now?: () => number;
}

const TERMINAL_STATUSES = new Set(["succeeded", "failed"]);

export class OperationPoller {
  private readonly intervalMs: number;
  private readonly maxAutoPollMs: number;
  private readonly visibility: VisibilitySource | null;
  private readonly now: () => number;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private abortController: AbortController | null = null;
  private operationId: string | null = null;
  private startedAt = 0;
  private hiddenAt: number | null = null;
  private running = false;
  private lastOperation: OperationResponse | null = null;
  private onUpdate: ((operation: OperationResponse) => void) | null = null;
  private onStop: ((event: PollStopEvent) => void) | null = null;

  constructor(private readonly gateway: WorkflowGateway, options: OperationPollerOptions = {}) {
    this.intervalMs = options.intervalMs ?? 1500;
    this.maxAutoPollMs = options.maxAutoPollMs ?? 15 * 60 * 1000;
    this.visibility = options.visibility ?? (typeof document === "undefined" ? null : document);
    this.now = options.now ?? Date.now;
  }

  start(
    operationId: string,
    onUpdate: (operation: OperationResponse) => void,
    onStop: (event: PollStopEvent) => void
  ): void {
    this.cancel(false);
    this.operationId = operationId;
    this.startedAt = this.now();
    this.hiddenAt = this.visibility?.hidden ? this.startedAt : null;
    this.onUpdate = onUpdate;
    this.onStop = onStop;
    this.visibility?.addEventListener("visibilitychange", this.handleVisibilityChange);
    if (!this.visibility?.hidden) void this.tick();
  }

  cancel(notify = true): void {
    if (!this.operationId && !this.running) return;
    const onStop = this.onStop;
    const lastOperation = this.lastOperation;
    this.cleanup();
    if (notify) onStop?.({ reason: "cancelled", operation: lastOperation });
  }

  private readonly handleVisibilityChange = (): void => {
    if (this.visibility?.hidden) {
      if (this.hiddenAt === null) this.hiddenAt = this.now();
      if (this.timer) clearTimeout(this.timer);
      this.timer = null;
      return;
    }
    if (this.hiddenAt !== null) {
      this.startedAt += this.now() - this.hiddenAt;
      this.hiddenAt = null;
    }
    if (!this.running && !this.timer && this.operationId) void this.tick();
  };

  private schedule(): void {
    if (!this.operationId || this.visibility?.hidden) return;
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.tick();
    }, this.intervalMs);
  }

  private async tick(): Promise<void> {
    if (this.running || !this.operationId || this.visibility?.hidden) return;
    if (this.now() - this.startedAt >= this.maxAutoPollMs) {
      const onStop = this.onStop;
      const lastOperation = this.lastOperation;
      this.cleanup();
      onStop?.({ reason: "timeout", operation: lastOperation });
      return;
    }

    this.running = true;
    this.abortController = new AbortController();
    try {
      const operation = await this.gateway.getOperation(this.operationId, this.abortController.signal);
      if (!this.operationId) return;
      this.lastOperation = operation;
      this.onUpdate?.(operation);
      if (TERMINAL_STATUSES.has(operation.status)) {
        const onStop = this.onStop;
        this.cleanup();
        onStop?.({ reason: "terminal", operation });
        return;
      }
    } catch (error) {
      if (!this.operationId || (error instanceof DOMException && error.name === "AbortError")) return;
      const onStop = this.onStop;
      const lastOperation = this.lastOperation;
      this.cleanup();
      onStop?.({ reason: "request_error", operation: lastOperation, error });
      return;
    } finally {
      this.running = false;
      this.abortController = null;
    }
    this.schedule();
  }

  private cleanup(): void {
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
    this.abortController?.abort();
    this.abortController = null;
    this.visibility?.removeEventListener("visibilitychange", this.handleVisibilityChange);
    this.operationId = null;
    this.hiddenAt = null;
    this.running = false;
    this.onUpdate = null;
    this.onStop = null;
  }
}
