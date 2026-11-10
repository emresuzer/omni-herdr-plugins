# todo-pane (herdr-app mod)

Read-only side panel, next to Files and Changes, for the focused pane's `TODO.md`: progress bar, running item with its timer, projects and phases, notes, and a **Show done** toggle. It reloads every 3 seconds.

`TODO.md` is looked up in the pane's folder, then in each parent up to the git root (or `$HOME`), then one folder down (the first `*/TODO.md` alphabetically). The format is the one the [`todo-md`](../todo-md/) popup reads.

## Install

1. Link the mod and copy its icon:

```bash
ln -s "$PWD" ~/.config/herdr-app/mods/todo
```

2. The tab bar's panel buttons are hardcoded in the shipped `tabs` mod, so clone it and add a button:

```bash
herdr-app do mod.clone --name tabs --as tabs-todo
cp todo.svg ~/.config/herdr-app/mods/tabs-todo/todo.svg
```

In `mods/tabs-todo/main.js`, make the panel list always include `todo` (the saved `workspace.panels` setting overrides a default), and add the button after Changes:

```js
const enabled = [...(shared("workspace.panels") ?? ["files", "changes"]), "todo"];
// ...
.when(enabled.includes("todo"), el => el.child(panelButton("todo", "TODO", "mods/tabs-todo/todo.svg")))
```

The icon must be a real file in the cloned folder; a symlinked folder's icons do not load.

3. Use a personal layout, because the app resets the shipped `main.json` on update and drops edits to it:

```bash
herdr-app do layout.clone --name main --as mine
```

In `layouts/mine.json`: set the `tabs` block's mod to `tabs-todo`, and add a child to the `panel-body` stack:

```json
{ "id": "todo", "type": "block", "content": { "mod": "todo" } }
```

Then `herdr-app do layout.switch --name mine`.
