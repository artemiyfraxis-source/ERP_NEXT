/**
 * Serializes editor saves and keeps one flush pending until every edit that
 * arrived before its completion is acknowledged by the server.
 */
export class DesignSaveCoordinator {
  private active: Promise<boolean> | null = null;
  private queued = false;

  constructor(private readonly saveOnce: () => Promise<boolean>) {}

  flush(): Promise<boolean> {
    if (this.active) {
      this.queued = true;
      return this.active;
    }

    const operation = this.drain();
    this.active = operation;
    return operation;
  }

  private async drain(): Promise<boolean> {
    try {
      do {
        this.queued = false;
        if (!(await this.saveOnce())) return false;
      } while (this.queued);
      return true;
    } finally {
      this.active = null;
    }
  }
}
