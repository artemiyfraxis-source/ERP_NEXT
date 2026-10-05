// @vitest-environment node
import { describe, expect, it, vi } from "vitest";
import { DesignSaveCoordinator } from "./design-save-coordinator";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => { resolve = complete; });
  return { promise, resolve };
}

describe("design save coordinator", () => {
  it("makes a quick navigation flush wait for edits queued during autosave", async () => {
    const first = deferred<boolean>();
    const second = deferred<boolean>();
    const saveOnce = vi.fn()
      .mockImplementationOnce(() => first.promise)
      .mockImplementationOnce(() => second.promise);
    const saves = new DesignSaveCoordinator(saveOnce);

    const autosave = saves.flush();
    const closeFlush = saves.flush();
    expect(closeFlush).toBe(autosave);
    expect(saveOnce).toHaveBeenCalledTimes(1);

    first.resolve(true);
    await first.promise;
    await Promise.resolve();
    expect(saveOnce).toHaveBeenCalledTimes(2);

    second.resolve(true);
    await expect(closeFlush).resolves.toBe(true);
  });

  it("starts the next flush normally after a completed save", async () => {
    const saveOnce = vi.fn().mockResolvedValue(true);
    const saves = new DesignSaveCoordinator(saveOnce);

    await expect(saves.flush()).resolves.toBe(true);
    await expect(saves.flush()).resolves.toBe(true);

    expect(saveOnce).toHaveBeenCalledTimes(2);
  });

  it("does not retry automatically after a failed save", async () => {
    const saveOnce = vi.fn().mockResolvedValue(false);
    const saves = new DesignSaveCoordinator(saveOnce);

    const autosave = saves.flush();
    const closeFlush = saves.flush();

    await expect(closeFlush).resolves.toBe(false);
    expect(autosave).toBe(closeFlush);
    expect(saveOnce).toHaveBeenCalledTimes(1);
  });
});
