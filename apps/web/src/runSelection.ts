/** Keep late replies in their own request generation; selection is a user action. */
export type RunTicket = { id: string; selection: number; request: number }
export class RunSelection {
  id: string | null = null
  revision = 0
  private sequence = 0
  private requests = new Map<string, number>()
  begin(id: string, select = false): RunTicket {
    if (select) {
      this.id = id
      this.revision++
    }
    const request = ++this.sequence
    this.requests.set(id, request)
    return { id, selection: this.revision, request }
  }
  latest(ticket: RunTicket): boolean {
    return this.requests.get(ticket.id) === ticket.request
  }
  selected(ticket: RunTicket): boolean {
    return this.id === ticket.id && this.revision === ticket.selection
  }
  current(revision: number): boolean {
    return this.revision === revision
  }
  visible(ticket: RunTicket): boolean {
    return this.latest(ticket) && this.selected(ticket)
  }
  clear(): void {
    this.id = null
    this.revision++
    this.requests.clear()
  }
}
