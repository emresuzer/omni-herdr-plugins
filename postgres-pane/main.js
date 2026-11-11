import { View, div } from "gpui-kit";
import { v_flex, h_flex, Scrollbar } from "gpui-base";
import { spaces, panes, focused, exec, readTextFile, appHome, theme, settings, run, props } from "herdr";

function location() {
  const space = spaces().find((s) => s.active), f = focused();
  const pane = f?.pane ? panes().find((p) => p.pane === f.pane) : null;
  if (pane?.mod) return { cwd: null, on: null }; // a mod pane (e.g. a rows tab) has no folder
  const on = f?.pane === null ? (f.terminal?.includes("/") ? f.terminal.split("/")[0] : null) : space?.machine ?? null;
  return { cwd: pane?.cwd || f?.cwd || space?.folder, on };
}
const key = () => JSON.stringify(location());
const num = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
const CELL_CHARS = 300; // pgview.py's cut, plus one char to tell a cut value

// pgview.py finds the .env, connects and answers JSON; run on the pane's machine, so it is sent as `python3 -c`.
async function pgview(args, on) {
  const source = await readTextFile(`${appHome()}/mods/postgres/pgview.py`);
  const r = await exec("python3", ["-c", source, ...args], { on, timeout: 60000 });
  if (r.code !== 0) throw new Error(r.stderr.trim().split("\n").pop() || `pgview exited ${r.code}`);
  return JSON.parse(r.stdout);
}

export default class Postgres extends View {
  init(_props, cx) {
    // Opened as a tab by a double-click: that table's last rows instead of the overview.
    if (props()?.rows) { this.target = props().rows; this.loadRows(cx); return; }
    this.data = null; this.error = null; this.loading = false; this.open = new Set(); this.db = {};
    this.refresh(cx);
  }

  refresh(cx) {
    const { cwd, on } = location(), context = key();
    if (!cwd && this.context) return; // a pane without a folder (e.g. a rows tab): keep the last project
    if (context !== this.context) { this.context = context; this.data = null; this.error = null; this.open = new Set(); }
    if (!cwd || this.loading) return;
    this.loading = true;
    cx.spawn(async (cx) => {
      try {
        const data = await pgview([cwd, this.db[context] ?? ""], on);
        if (key() !== context) return;
        this.data = data; this.error = null;
      } catch (e) {
        if (key() === context) { this.data = null; this.error = String(e); }
      } finally {
        this.loading = false; cx.notify();
        if (key() !== context) this.refresh(cx);
      }
    });
  }

  pick(db, cx) { this.db[this.context] = db; this.open = new Set(); this.refresh(cx); cx.notify(); }
  showRows(r) {
    const { cwd, on } = location();
    run("tab.new", { name: r.name, mod: "postgres", props: { rows: { cwd, on, db: this.data.db, schema: r.schema, table: r.name } } });
  }

  loadRows(cx) {
    if (this.loading) return;
    const { cwd, on, db, schema, table } = this.target;
    this.loading = true; cx.notify();
    cx.spawn(async (cx) => {
      try { this.data = await pgview([cwd, db, schema, table], on); this.error = null; }
      catch (e) { this.data = null; this.error = String(e); }
      finally { this.loading = false; cx.notify(); }
    });
  }

  renderRows() {
    const t = theme(), d = this.data, ok = d?.status === "ok", { db, schema, table } = this.target;
    const cols = ok ? d.columns : [], rows = ok ? d.rows : [];
    const cell = (v) => v === null ? null : (v.length > CELL_CHARS ? v.slice(0, CELL_CHARS) + "…" : v).replace(/\r?\n/g, " ⏎ ");
    const widths = cols.map((c, i) => Math.min(320, Math.max(64, 16 + 7.5 * Math.min(40, Math.max(c.length, ...rows.map((r) => cell(r[i])?.length ?? 4))))));
    const total = widths.reduce((a, b) => a + b, 0);
    const line = (values, i, head) => h_flex().w(total).flex_none().font_family(String(settings().font_family)).text_size(12).border_b(1).border_color(t.border)
      .bg(head ? t.raised : i % 2 ? t.chrome : t.background)
      .children(values.map((v, j) => div().w(widths[j]).flex_none().px(8).py(3).truncate()
        .text_color(head ? (d.pk.includes(v) ? t.accent : t.foreground) : v === null ? t.dim : t.foreground)
        .when(head, (e) => e.font_semibold())
        .child(head ? v : v === null ? "NULL" : cell(v))));
    const message = this.error ?? (!d ? "Loading…" : d.status === "none" ? "No database settings in .env any more" : d.status === "error" ? d.error : !rows.length ? "No rows" : null);
    const order = ok ? (d.order === "primary key" ? `newest by ${d.pk.join(", ")}` : d.order) : "";

    return v_flex().id("pg-rows").size_full().min_h(0).bg(t.background).text_color(t.foreground).text_size(13)
      .child(h_flex().flex_none().px(14).py(8).gap(8).items_center().border_b(1).border_color(t.border)
        .child(div().font_semibold().child(schema === "public" ? table : `${schema}.${table}`))
        .child(div().text_size(11).text_color(t.dim).child(`${db}${ok ? ` · last ${rows.length} ${rows.length === 1 ? "row" : "rows"} · ${order}` : ""}`))
        .child(div().flex_1())
        .child(div().id("pg-rows-refresh").px(8).py(3).rounded(6).text_size(11).cursor_pointer().bg(t.raised).text_color(t.muted).hover((s) => s.bg(t.hover))
          .on_click((_e, cx) => this.loadRows(cx)).child(this.loading ? "Loading…" : "Refresh")))
      .child(div().flex_1().min_h(0).relative()
        .child(v_flex().id("pg-grid").absolute().inset_0().overflow_scroll().pb(10).pr(10)
          .when(ok && cols.length > 0, (e) => e.child(line(cols, 0, true)).children(rows.map((r, i) => line(r, i, false))))
          .when(Boolean(message), (e) => e.child(div().p(12).text_color(this.error || d?.status === "error" ? t.blocked : t.dim).child(message))))
        .child(Scrollbar.new("pg-grid").mode("always").absolute().inset_0()));
  }

  toggle(id, cx) { this.open.has(id) ? this.open.delete(id) : this.open.add(id); cx.notify(); }

  relation(r, t) {
    const id = `${r.schema}.${r.name}`, open = this.open.has(id);
    const rows = r.rows === null ? "—" : `${r.exact ? "" : "~"}${num(r.rows)}`;
    return v_flex().w_full().flex_none()
      .child(h_flex().id(`rel-${id}`).px(8).py(3).gap(6).items_center().rounded(6).cursor_pointer().hover((s) => s.bg(t.hover))
        .on_click((e, cx) => { this.toggle(id, cx); if (e.click_count === 2) this.showRows(r); })
        .child(div().w(10).flex_none().text_size(10).text_color(t.dim).child(open ? "▾" : "▸"))
        .when(r.schema !== "public", (e) => e.child(div().flex_none().text_color(t.dim).child(`${r.schema}.`)))
        .child(div().flex_1().min_w(0).truncate().child(r.name))
        .child(div().flex_none().text_size(11).text_color(t.muted).child(rows)))
      .when(open, (e) => e.children(r.columns.map((c) => h_flex().pl(30).pr(8).py(1).gap(6).items_center().text_size(12)
        .child(div().flex_none().text_color(c.pk ? t.accent : t.foreground).child(c.name))
        .when(c.pk, (e) => e.child(div().flex_none().px(3).rounded(3).border_1().border_color(t.border).text_size(9).text_color(t.accent).child("PK")))
        .child(div().flex_1().min_w(0).truncate().text_color(t.dim).child(`${c.type}${c.notnull ? "" : "?"}`)))));
  }

  render(cx) {
    if (this.target) return this.renderRows();
    if (key() !== this.context && location().cwd) this.refresh(cx);
    const t = theme(), d = this.data, ok = d?.status === "ok";
    const tables = ok ? d.relations.filter((r) => !r.view) : [], views = ok ? d.relations.filter((r) => r.view) : [];
    const section = (title, list) => [
      h_flex().px(8).pt(10).pb(2).gap(8).items_center()
        .child(div().text_size(11).text_color(t.muted).child(title))
        .child(div().flex_1())
        .child(div().text_size(11).text_color(t.dim).child(String(list.length))),
      ...list.map((r) => this.relation(r, t)),
    ];
    const message = this.error ?? (!JSON.parse(this.context ?? "{}").cwd ? "No folder"
      : !d ? (this.loading ? "Connecting…" : null)
      : d.status === "none" ? (d.env ? `No OCCOS_DATABASE_URL or POSTGRES_PASSWORD in ${d.env}` : "No .env here or in a parent folder")
      : d.status === "error" ? d.error : null);

    return v_flex().id("postgres").size_full().min_h(0).rounded(10).overflow_hidden().bg(t.background).border_1().border_color(t.border).text_color(t.foreground).text_size(13)
      .child(v_flex().flex_none().px(14).py(10).gap(6).border_b(1).border_color(t.border)
        .child(h_flex().gap(8).items_center()
          .child(div().font_semibold().child("Postgres"))
          .when(ok, (e) => e.child(div().px(5).py(1).rounded(4).border_1().border_color(t.border).text_size(10).text_color(t.muted).child(`${tables.length} tables · ${views.length} views`)))
          .child(div().flex_1())
          .child(div().id("pg-refresh").px(8).py(3).rounded(6).text_size(11).cursor_pointer().bg(t.raised).text_color(t.muted).hover((s) => s.bg(t.hover))
            .on_click((_e, cx) => { this.refresh(cx); cx.notify(); }).child(this.loading ? "Loading…" : "Refresh")))
        .when(Boolean(d?.user), (e) => e.child(div().text_size(11).text_color(t.dim).truncate().child(`${d.user}@${d.host}:${d.port} · ${d.source}`)))
        .when(ok && d.databases.length > 0, (e) => e.child(h_flex().flex_wrap().gap(4)
          .children(d.databases.map((name) => div().id(`db-${name}`).px(7).py(2).rounded(5).text_size(11).cursor_pointer()
            .bg(name === d.db ? t.selected : t.raised).text_color(name === d.db ? t.foreground : t.muted).hover((s) => s.bg(t.hover))
            .on_click((_e, cx) => this.pick(name, cx)).child(name))))))
      .child(div().flex_1().min_h(0).relative()
        .child(v_flex().id("pg-list").absolute().inset_0().overflow_y_scroll().p(6).pb(14)
          .when(ok, (e) => e.children([...section("TABLES", tables), ...section("VIEWS", views)]))
          .when(Boolean(message), (e) => e.child(div().p(8).text_color(this.error || d?.status === "error" ? t.blocked : t.dim).child(message))))
        .child(Scrollbar.vertical("pg-list").absolute().inset_0()));
  }
}
