# Flow-diagram generators

Regenerates `docs/langgraph-engine-flow-{overview,detailed}.{excalidraw,png}`.

Layout is computed by [dagre](https://github.com/dagrejs/dagre) (the same layered
engine Mermaid uses) rather than hand-placed coordinates — hand-placing 26 nodes
and ~49 edges does not survive contact with edits.

```bash
cd docs/diagram-src
npm install dagre          # only dependency; not committed
node layout.js             # graph spec -> layout-{overview,detailed}.json
python3 render.py overview # -> docs/langgraph-engine-flow-overview.{png,excalidraw}
python3 render.py detailed # -> docs/langgraph-engine-flow-detailed.{png,excalidraw}
```

`render.py` needs Pillow (system `python3` has it; the agent venv does not).

## When to regenerate

The graph spec lives in `layout.js` (`overview`, `mainPanel`, `cfixPanel`) and is a
**hand-maintained mirror** of `play-to-spring-kit/agent/graph.py`'s `build_graph()`.
It is not derived from the code automatically — if you add/remove a node or edge
there, update `layout.js` and re-run, or the diagrams silently drift.

Both `.png` (previewable in GitHub/editors) and `.excalidraw` (editable at
excalidraw.com) are emitted from the same layout, so they never disagree.

Note: arrows in the `.excalidraw` output are not bound to their boxes — dragging a
box will not drag its arrows. Rearranging by hand means reconnecting them; prefer
editing `layout.js` and regenerating.
