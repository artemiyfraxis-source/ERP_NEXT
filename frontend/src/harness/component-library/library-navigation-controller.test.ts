import { describe, expect, it, vi } from "vitest";
import { LibraryNavigationController, librarySaveAcknowledgesCurrentEdit, type LibraryNavigationChoice } from "./library-navigation-controller";

function options(choice: LibraryNavigationChoice = "save") {
  return { hasChanges: true, busy: false, choose: vi.fn(async () => choice), save: vi.fn(async () => true), discard: vi.fn(() => true) };
}

describe("library navigation protection", () => {
  it("allows an acknowledged draft without publishing it again", async () => {
    const input = { ...options(), hasChanges: false };
    expect(await new LibraryNavigationController().request(input)).toBe(true);
    expect(input.choose).not.toHaveBeenCalled(); expect(input.save).not.toHaveBeenCalled();
  });
  it("keeps the editor on cancel or failed save and never discards implicitly", async () => {
    const controller = new LibraryNavigationController();
    const cancel = options("stay");
    expect(await controller.request(cancel)).toBe(false);
    expect(cancel.discard).not.toHaveBeenCalled(); expect(cancel.save).not.toHaveBeenCalled();
    const failed = { ...options(), save: vi.fn(async () => false) };
    expect(await controller.request(failed)).toBe(false);
    expect(failed.discard).not.toHaveBeenCalled();
    expect(await controller.request(options())).toBe(true);
  });
  it("requires successful explicit discard and blocks busy navigation", async () => {
    const discard = options("discard");
    expect(await new LibraryNavigationController().request(discard)).toBe(true);
    expect(discard.discard).toHaveBeenCalledOnce(); expect(discard.save).not.toHaveBeenCalled();
    expect(await new LibraryNavigationController().request({ ...options("discard"), discard: () => false })).toBe(false);
    const busy = { ...options(), busy: true };
    expect(await new LibraryNavigationController().request(busy)).toBe(false);
    expect(busy.choose).not.toHaveBeenCalled();
  });
  it("serializes open/new/section transitions while a dialog or save is pending", async () => {
    let answer!: (choice: LibraryNavigationChoice) => void;
    const controller = new LibraryNavigationController();
    const first = controller.request({ ...options(), choose: () => new Promise(resolve => { answer = resolve; }) });
    expect(await controller.request(options())).toBe(false);
    answer("save"); expect(await first).toBe(true);
    expect(await controller.request(options())).toBe(true);
  });
  it("does not acknowledge edits made after the save request started", () => {
    expect(librarySaveAcknowledgesCurrentEdit(4, 4)).toBe(true);
    expect(librarySaveAcknowledgesCurrentEdit(4, 5)).toBe(false);
  });
});
