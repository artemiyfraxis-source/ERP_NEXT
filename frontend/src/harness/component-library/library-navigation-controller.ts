export type LibraryNavigationChoice = "save" | "discard" | "stay";

/** Serializes all destructive library transitions, including application navigation. */
export class LibraryNavigationController {
  private pending = false;

  async request(input: {
    hasChanges: boolean;
    busy: boolean;
    choose: () => Promise<LibraryNavigationChoice>;
    save: () => Promise<boolean>;
    discard: () => boolean;
  }): Promise<boolean> {
    if (this.pending || input.busy) return false;
    if (!input.hasChanges) return true;
    this.pending = true;
    try {
      const choice = await input.choose();
      if (choice === "stay") return false;
      if (choice === "discard") return input.discard();
      return await input.save();
    } finally {
      this.pending = false;
    }
  }
}

/** A server ACK must not mark edits made during the request as saved. */
export function librarySaveAcknowledgesCurrentEdit(startedAt: number, current: number): boolean {
  return startedAt === current;
}
