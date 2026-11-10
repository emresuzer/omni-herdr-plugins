import { View, div } from "gpui-kit";
import { spaces, panes, run, home } from "herdr";

const GENERIC = new Set(["bash", "zsh", "sh", "fish", "claude", "claude code", "node", "python", "python3"]);
const MAX = 32;

function clean(title) {
  const t = (title ?? "").replace(/^[^\p{L}\p{N}~/]+/u, "").trim();
  return t && !GENERIC.has(t.toLowerCase()) ? t : null;
}

function folder(cwd) {
  if (!cwd) return "~";
  const trimmed = cwd.replace(/\/+$/, "");
  return trimmed === home().replace(/\/+$/, "") || trimmed === "" ? "~" : trimmed.split("/").pop();
}

function cut(s) {
  return s.length > MAX ? s.slice(0, MAX - 1) + "…" : s;
}

// Tabs this mod named, by id: a named tab whose name differs from it was renamed by hand.
export default class AutoTabName extends View {
  init() {
    try { this.mine = JSON.parse(localStorage.getItem("mine") ?? "{}"); } catch { this.mine = {}; }
  }

  target(tabId, all) {
    const own = all.filter((p) => p.tab === tabId && p.terminal);
    const p = own.find((x) => x.focused) ?? own[0];
    if (!p) return null;
    return cut((p.app && clean(p.title)) || folder(p.cwd));
  }

  render() {
    const all = panes();
    const live = new Set();
    let changed = false;
    for (const s of spaces()) {
      if (s.machine) continue;
      for (const t of s.tabs) {
        live.add(t.id);
        if (t.named && this.mine[t.id] !== t.name) continue;
        const want = this.target(t.id, all);
        if (!want || want === t.name) continue;
        run("tab.rename", { id: t.id, name: want });
        this.mine[t.id] = want;
        changed = true;
      }
    }
    for (const id of Object.keys(this.mine)) if (!live.has(id)) { delete this.mine[id]; changed = true; }
    if (changed) try { localStorage.setItem("mine", JSON.stringify(this.mine)); } catch {}
    return div();
  }
}
