# auto-tab-name (herdr-app mod)

Names each tab after the focused terminal's agent session title (Claude's `/rename` or AI title), or the folder when no agent runs. Tabs renamed by hand are never touched; `tab.rename` with an empty name hands the tab back to the mod. Local spaces only.

Install: symlink into the app's mods folder and add a 1px block to the layout so the mod stays alive:

```bash
ln -s "$PWD" ~/.config/herdr-app/mods/auto-tab-name
```

```json
{ "id": "auto-tab-name", "type": "block", "size": { "px": 1 }, "content": { "mod": "auto-tab-name" } }
```

Add that block as a child of `work` in `layouts/main.json`. A `hidden` block never starts the mod. Other layouts need the same block.
