import { afterEach, describe, expect, it, vi } from "vitest";

import type { WorkflowGateway } from "./gateway";
import { OperationPoller, type VisibilitySource } from "./operation-poller";
import type { OperationResponse } from "./types";

function operation(status: OperationResponse["status"]): OperationResponse {
  return {
    operation_id: "analysis_1",
    kind: "case_workflow",
    stage: "analysis",
    status,
    case_id: "case_1",
    analysis_id: "analysis_1",
    draft_id: null,
    progress: { current: 1, total: 2, percent: 50, current_item: null },
    failure: status === "failed" ? { code: "ANALYSIS_FAILED", message: "分析失败", retryable: true } : null,
    created_at: "2026-08-22T00:00:00Z",
    updated_at: "2026-08-22T00:00:01Z"
  };
}

function gateway(getOperation: WorkflowGateway["getOperation"]): WorkflowGateway {
  return { getOperation } as WorkflowGateway;
}

class TestVisibility implements VisibilitySource {
  hidden = false;
  private listener: (() => void) | null = null;
  addEventListener(_type: "visibilitychange", listener: () => void): void {
    this.listener = listener;
  }
  removeEventListener(): void {
    this.listener = null;
  }
  setHidden(hidden: boolean): void {
    this.hidden = hidden;
    this.listener?.();
  }
}

describe("OperationPoller", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("stops on a terminal operation", async () => {
    const onUpdate = vi.fn();
    const onStop = vi.fn();
    const poller = new OperationPoller(gateway(vi.fn(async () => operation("succeeded"))), {
      visibility: null
    });

    poller.start("analysis_1", onUpdate, onStop);
    await vi.waitFor(() => expect(onStop).toHaveBeenCalledTimes(1));

    expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ status: "succeeded" }));
    expect(onStop).toHaveBeenCalledWith(expect.objectContaining({ reason: "terminal" }));
  });

  it("never overlaps requests and can be cancelled", async () => {
    vi.useFakeTimers();
    let resolveFirst!: (value: OperationResponse) => void;
    const getOperation = vi.fn(
      () => new Promise<OperationResponse>((resolve) => { resolveFirst = resolve; })
    );
    const onStop = vi.fn();
    const poller = new OperationPoller(gateway(getOperation), {
      intervalMs: 100,
      visibility: null
    });

    poller.start("analysis_1", vi.fn(), onStop);
    await vi.advanceTimersByTimeAsync(1000);
    expect(getOperation).toHaveBeenCalledTimes(1);

    resolveFirst(operation("running"));
    await Promise.resolve();
    await vi.advanceTimersByTimeAsync(100);
    expect(getOperation).toHaveBeenCalledTimes(2);

    poller.cancel();
    expect(onStop).toHaveBeenCalledWith(expect.objectContaining({ reason: "cancelled" }));
  });

  it("pauses while hidden and resumes without declaring failure", async () => {
    vi.useFakeTimers();
    const visibility = new TestVisibility();
    visibility.hidden = true;
    const getOperation = vi.fn(async () => operation("succeeded"));
    const onStop = vi.fn();
    const poller = new OperationPoller(gateway(getOperation), { visibility });

    poller.start("analysis_1", vi.fn(), onStop);
    await vi.advanceTimersByTimeAsync(5000);
    expect(getOperation).not.toHaveBeenCalled();

    visibility.setHidden(false);
    await vi.advanceTimersByTimeAsync(0);
    expect(getOperation).toHaveBeenCalledTimes(1);
    expect(onStop).toHaveBeenCalledWith(expect.objectContaining({ reason: "terminal" }));
  });

  it("does not count hidden wall time toward the automatic polling limit", async () => {
    vi.useFakeTimers();
    let now = 1_000;
    const visibility = new TestVisibility();
    const getOperation = vi.fn(async () => operation(getOperation.mock.calls.length >= 2 ? "succeeded" : "running"));
    const onStop = vi.fn();
    const poller = new OperationPoller(gateway(getOperation), {
      intervalMs: 100,
      maxAutoPollMs: 500,
      visibility,
      now: () => now
    });

    poller.start("analysis_1", vi.fn(), onStop);
    await vi.advanceTimersByTimeAsync(0);
    visibility.setHidden(true);
    now += 60_000;
    visibility.setHidden(false);
    await vi.advanceTimersByTimeAsync(100);

    expect(getOperation).toHaveBeenCalledTimes(2);
    expect(onStop).toHaveBeenCalledWith(expect.objectContaining({ reason: "terminal" }));
  });

  it("stops automatic polling at the time bound without marking the operation failed", async () => {
    vi.useFakeTimers();
    let now = 0;
    const getOperation = vi.fn(async () => operation("running"));
    const onStop = vi.fn();
    const poller = new OperationPoller(gateway(getOperation), {
      intervalMs: 100,
      maxAutoPollMs: 500,
      visibility: null,
      now: () => now
    });

    poller.start("analysis_1", vi.fn(), onStop);
    await vi.advanceTimersByTimeAsync(0);
    now = 500;
    await vi.advanceTimersByTimeAsync(100);

    expect(onStop).toHaveBeenCalledWith(expect.objectContaining({
      reason: "timeout",
      operation: expect.objectContaining({ status: "running" })
    }));
  });
});
