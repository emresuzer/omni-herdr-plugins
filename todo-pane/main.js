import { View, div } from "gpui-kit";
import { v_flex, h_flex, Scrollbar } from "gpui-base";
import { spaces, panes, focused, exec, readTextFile, home, theme } from "herdr";

const ITEM = /^[-*] \[([ ~xX>\-])\] (?:#(\d+)\s+)?(.*)$/;
const NOTE = /^\s+[-*] (?:note: )?(.*)$/;
const DONE = "xX-";

function location() {
  const space = spaces().find((s) => s.active), f = focused();
  const pane = f?.pane ? panes().find((p) => p.pane === f.pane) : null;
  const on = f?.pane === null ? (f.terminal?.includes("/") ? f.terminal.split("/")[0] : null) : space?.machine ?? null;
  return { cwd: pane?.cwd || f?.cwd || space?.folder, on };
}
const key = () => JSON.stringify(location());

// Projects (`# `) hold phases (`## `) hold items; indented lines under an item are its notes.
export function parse(text) {
  const phases = [];
  let project = "", phase = { title: "", project, items: [] }, last = null;
  const close = () => { if (phase.items.length || phase.title) phases.push(phase); };
  text.split("\n").forEach((line, i, lines) => {
    if (line.startsWith("# ")) { close(); project = line.slice(2).trim(); phase = { title: "", project, items: [] }; last = null; return; }
    if (line.startsWith("## ")) { close(); phase = { title: line.slice(3).trim(), project, items: [] }; last = null; return; }
    const m = ITEM.exec(line);
    if (m) { last = { marker: m[1], num: m[2] ? +m[2] : null, text: m[3].trim(), notes: [] }; phase.items.push(last); return; }
    if (last && /^\s+\S/.test(line)) { const n = NOTE.exec(line); last.notes.push(n ? n[1] : line.trim()); return; }
    last = null;
  });
  close();
  return phases;
}

const ago = (ms) => { const m = Math.floor(ms / 60000); return m < 1 ? "<1m" : m < 60 ? `${m}m` : `${Math.floor(m / 60)}h ${m % 60}m`; };

export default class Todo extends View {
  init(_props, cx) {
    this.text = null; this.path = null; this.showDone = false; this.seen = {};
    this.refresh(cx);
    cx.timer.every(3000, (cx) => this.refresh(cx));
  }

  // TODO.md in the focused folder, else in each parent up to the git root (or $HOME), else one folder down.
  refresh(cx) {
    if (this.loading) return;
    const { cwd, on } = location(), context = key();
    if (context !== this.context) { this.context = context; this.text = null; this.path = null; this.bound = null; }
    if (!cwd) return;
    this.loading = true;
    cx.spawn(async (cx) => {
      try {
        if (this.bound === null) {
          const top = await exec("git", ["rev-parse", "--show-toplevel"], { cwd, on });
          this.bound = top.code === 0 ? top.stdout.trim() : home().replace(/\/+$/, "");
        }
        let dir = cwd.replace(/\/+$/, ""), found = null, text = null;
        for (;;) {
          text = await readTextFile(`${dir}/TODO.md`, { on }).catch(() => null);
          if (text !== null) { found = `${dir}/TODO.md`; break; }
          if (dir === this.bound || dir === "" || !dir.includes("/")) break;
          dir = dir.slice(0, dir.lastIndexOf("/"));
        }
        if (text === null) {
          const down = await exec("sh", ["-c", 'ls -d "$1"/*/TODO.md 2>/dev/null | head -1', "todo", cwd.replace(/\/+$/, "")], { on });
          const sub = down.stdout.trim();
          if (sub) { text = await readTextFile(sub, { on }).catch(() => null); if (text !== null) found = sub; }
        }
        if (key() !== context) return;
        if (found !== this.path || text !== this.text) { this.path = found; this.text = text; }
      } finally { this.loading = false; cx.notify(); }
    });
  }

  toggleDone(cx) { this.showDone = !this.showDone; cx.notify(); }

  itemRow(it, t) {
    const glyph = { " ": "○", "~": "◐", x: "✓", X: "✓", ">": "→", "-": "⊘" }[it.marker];
    const color = it.marker === "~" ? t.working : DONE.includes(it.marker) && it.marker !== "-" ? t.ansi2 : it.marker === "-" || it.marker === ">" ? t.dim : t.muted;
    const waiting = /\(waiting on you\)\s*$/.test(it.text);
    return v_flex().w_full().flex_none().px(8).py(4).gap(2).rounded(7)
      .child(h_flex().gap(8).items_start()
        .child(div().w(14).flex_none().text_color(color).child(glyph))
        .when(it.num !== null, (e) => e.child(div().flex_none().text_color(t.dim).text_size(11).child(`#${it.num}`)))
        .child(div().flex_1().min_w(0).text_color(DONE.includes(it.marker) ? t.dim : waiting ? t.blocked : t.foreground).child(it.text)))
      .children(it.notes.map((n) => div().pl(22).text_size(11).text_color(t.muted).child(`↳ ${n}`)));
  }

  render(cx) {
    if (key() !== this.context) this.refresh(cx);
    const t = theme(), now = Date.now();
    const phases = this.text === null ? [] : parse(this.text);
    const all = phases.flatMap((p) => p.items);
    const done = all.filter((i) => DONE.includes(i.marker)).length, pct = all.length ? Math.round((done / all.length) * 100) : 0;
    const running = all.find((i) => i.marker === "~");
    const live = new Set();
    for (const i of all) if (i.marker === "~") { const k = `${this.path}#${i.num}`; live.add(k); this.seen[k] ??= now; }
    for (const k of Object.keys(this.seen)) if (!live.has(k)) delete this.seen[k];

    const rows = [];
    let project = null;
    for (const p of phases) {
      const open = p.items.filter((i) => !DONE.includes(i.marker));
      const shown = this.showDone ? p.items : open;
      if (!shown.length) continue;
      if (p.project && p.project !== project) { project = p.project; rows.push(div().px(8).pt(12).pb(2).font_semibold().text_color(t.foreground).child(p.project)); }
      const d = p.items.length - open.length;
      rows.push(h_flex().px(8).pt(8).pb(2).gap(8).items_center()
        .child(div().text_size(11).text_color(t.muted).child((p.title || "—").toUpperCase()))
        .child(div().flex_1())
        .child(div().text_size(11).text_color(t.dim).child(`${d}/${p.items.length}`)));
      for (const it of shown) rows.push(this.itemRow(it, t));
    }
    const empty = this.text === null ? (this.context && JSON.parse(this.context).cwd ? "No TODO.md here or in a parent folder" : "No folder") : !rows.length ? "Nothing open" : null;

    return v_flex().id("todo").size_full().min_h(0).rounded(10).overflow_hidden().bg(t.background).border_1().border_color(t.border).text_color(t.foreground).text_size(13)
      .child(v_flex().flex_none().px(14).py(10).gap(8).border_b(1).border_color(t.border)
        .child(h_flex().gap(8).items_center()
          .child(div().font_semibold().child("TODO"))
          .child(div().px(5).py(1).rounded(4).border_1().border_color(t.border).text_size(10).text_color(t.muted).child(`${done}/${all.length}`))
          .child(div().flex_1())
          .child(div().id("todo-done").px(8).py(3).rounded(6).text_size(11).cursor_pointer().bg(this.showDone ? t.selected : t.raised).text_color(this.showDone ? t.foreground : t.muted).hover((s) => s.bg(t.hover)).on_click((_e, cx) => this.toggleDone(cx)).child("Show done")
          ))
        .when(all.length > 0, (e) => e.child(h_flex().gap(8).items_center()
          .child(div().flex_1().h(6).rounded(3).bg(t.raised).overflow_hidden().child(div().h_full().w(`${pct}%`).bg(t.ansi2)))
          .child(div().text_size(11).text_color(t.muted).child(`${pct}%`)))
        )
        .when(Boolean(running), (e) => e.child(h_flex().gap(6).items_center().text_size(12)
          .child(div().text_color(t.working).child("◐"))
          .child(div().flex_1().min_w(0).truncate().child(running.num !== null ? `#${running.num} ${running.text}` : running.text))
          .child(div().text_size(11).text_color(t.dim).child(ago(now - this.seen[`${this.path}#${running.num}`]))))))
      .child(div().flex_1().min_h(0).relative()
        .child(v_flex().id("todo-list").absolute().inset_0().overflow_y_scroll().p(6).pb(14).children(rows)
          .when(Boolean(empty), (e) => e.child(div().p(8).text_color(t.dim).child(empty))))
        .child(Scrollbar.vertical("todo-list").absolute().inset_0()));
  }
}
