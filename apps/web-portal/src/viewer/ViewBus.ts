export interface ViewState { yaw: number; pitch: number; fov: number; aspect: number }
type Listener = (s: ViewState, source: string) => void;

/** Tiny mutable pub/sub that keeps the panorama and BIM cameras in sync without re-rendering React. */
export class ViewBus {
  state: ViewState = { yaw: 0, pitch: 0, fov: 75, aspect: 1.6 };
  private ls = new Set<Listener>();
  set(p: Partial<ViewState>, source: string) {
    const n = { ...this.state, ...p };
    n.pitch = Math.max(-1.45, Math.min(1.45, n.pitch));
    n.fov = Math.max(25, Math.min(100, n.fov));
    const TAU = Math.PI * 2;
    n.yaw = ((((n.yaw + Math.PI) % TAU) + TAU) % TAU) - Math.PI;
    this.state = n;
    this.ls.forEach((l) => l(n, source));
  }
  subscribe(l: Listener) { this.ls.add(l); return () => { this.ls.delete(l); }; }
}
