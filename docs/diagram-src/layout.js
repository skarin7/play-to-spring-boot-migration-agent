const dagre = require('dagre');
const fs = require('fs');

const C = {
  bootstrap: { stroke: '#e8590c', bg: '#ffec99' },
  inventory: { stroke: '#1971c2', bg: '#a5d8ff' },
  slice:     { stroke: '#6741d9', bg: '#e5dbff' },
  cfix:      { stroke: '#9c36b5', bg: '#eebefa' },
  routes:    { stroke: '#e03131', bg: '#ffd6d6' },
  cfg:       { stroke: '#2f9e44', bg: '#b2f2bb' },
  boot:      { stroke: '#e8590c', bg: '#ffe3c2' },
  term_ok:   { stroke: '#2f9e44', bg: '#d3f9d8' },
  term_bad:  { stroke: '#c92a2a', bg: '#ffe3e3' },
};
const NW = 186, NH = 64;

function newGraph(ranksep) {
  const g = new dagre.graphlib.Graph({ compound: true, multigraph: true });
  g.setGraph({ rankdir: 'LR', nodesep: 42, ranksep: ranksep || 78, marginx: 26, marginy: 26 });
  g.setDefaultEdgeLabel(() => ({}));
  return g;
}

function build(spec) {
  const g = newGraph(spec.ranksep);
  spec.clusters?.forEach((c) => g.setNode(c.id, { label: c.label, isCluster: true, pal: c.pal }));
  // Rail-placed nodes (e.g. run_halt) are positioned in post-processing, not by dagre —
  // they have no forward edges, so dagre would otherwise strand them at an arbitrary rank.
  const railNodes = spec.nodes.filter((n) => n.onRail);
  spec.nodes.filter((n) => !n.onRail).forEach((n) => {
    g.setNode(n.id, { label: n.label, sub: n.sub || '', pal: n.pal, warn: !!n.warn,
                      width: n.w || NW, height: n.h || NH });
    if (n.parent) g.setParent(n.id, n.parent);
  });
  spec.edges.forEach((e) => {
    if (e.a === e.b) return; // self-loops drawn by the renderer, not dagre (dagre routes them badly)
    g.setEdge(e.a, e.b, {
      label: e.label || '', kind: e.kind || 'normal',
      weight: e.kind === 'abort' ? 1 : (e.weight || 6),
      minlen: e.minlen || 1,
      width: e.label ? Math.min(e.label.length * 6, 160) : 0, height: e.label ? 16 : 0,
    }, `${e.a}->${e.b}->${e.label || ''}`);
  });
  dagre.layout(g);
  const out = { nodes: [], clusters: [], edges: [], selfLoops: [], graph: g.graph() };
  g.nodes().forEach((id) => {
    const n = g.node(id);
    if (!n) return;
    const rec = { id, x: n.x, y: n.y, width: n.width, height: n.height, label: n.label,
                  sub: n.sub || '', pal: n.pal, warn: !!n.warn };
    if (n.isCluster) out.clusters.push(rec); else out.nodes.push(rec);
  });
  g.edges().forEach((e) => {
    const ed = g.edge(e);
    out.edges.push({ v: e.v, w: e.w, points: ed.points, label: ed.label, kind: ed.kind, x: ed.x, y: ed.y });
  });
  spec.edges.filter((e) => e.a === e.b).forEach((e) => out.selfLoops.push({ id: e.a, label: e.label }));

  if (spec.abortRail) {
    const idx = Object.fromEntries(out.nodes.map((n) => [n.id, n]));
    const sources = spec.abortRail.from.map((id) => idx[id]).filter(Boolean);
    const maxBottom = Math.max(...out.nodes.map((n) => n.y + n.height / 2));
    const railY = maxBottom + 96;
    const railNode = railNodes.find((n) => n.id === spec.abortRail.to);
    const rightmost = Math.max(...out.nodes.map((n) => n.x + n.width / 2));
    const placed = {
      id: railNode.id, label: railNode.label, sub: railNode.sub || '', pal: railNode.pal,
      width: railNode.w || NW, height: railNode.h || NH,
      x: rightmost - (railNode.w || NW) / 2, y: railY,
    };
    out.nodes.push(placed);
    out.abortRail = {
      y: railY,
      label: spec.abortRail.label,
      stubs: sources.map((n) => ({ x: n.x, from: n.y + n.height / 2 })),
      x0: Math.min(...sources.map((n) => n.x)),
      x1: placed.x - placed.width / 2,
    };
    out.graph.height = railY + placed.height / 2 + 40;
  }
  if (spec.legend) { out.legend = spec.legend; out.graph.height += 34; }
  return out;
}

// ===================================================================== OVERVIEW
const overview = {
  ranksep: 96,
  nodes: [
    { id: 'prepare', label: 'PREPARE', sub: 'build JAR · scaffold Spring project\n· scan Play surface · find units',
      pal: C.bootstrap, w: 266, h: 92, warn: true },
    { id: 'migrate', label: 'MIGRATE  ↺ per unit', sub: 'transform (skip gap files)\n→ compile-fix loop → finalize',
      pal: C.slice, w: 266, h: 92, warn: true, loops: true },
    { id: 'reconcile', label: 'RECONCILE  ↺', sub: 'routes → @*Mapping gaps\nconfig → leftover HOCON keys',
      pal: C.routes, w: 266, h: 92, warn: true, loops: true },
    { id: 'prove', label: 'PROVE IT RUNS  ↺', sub: 'layer-count verify · boot the app\n→ fix wiring → retry',
      pal: C.boot, w: 266, h: 92, warn: true, loops: true },
    { id: 'run_done', label: 'RUN_DONE', sub: 'app started · exit 0', pal: C.term_ok, w: 216, h: 92 },
    { id: 'run_halt', label: 'RUN_HALT', sub: 'exit 4 / 5 / 6', pal: C.term_bad, w: 216, h: 92 },
  ],
  edges: [
    { a: 'prepare', b: 'migrate', label: 'units found', weight: 20 },
    { a: 'migrate', b: 'migrate', label: 'next unit' },
    { a: 'migrate', b: 'reconcile', label: 'all units done', weight: 20 },
    { a: 'reconcile', b: 'reconcile', label: 'more gaps / keys' },
    { a: 'reconcile', b: 'prove', weight: 20 },
    { a: 'prove', b: 'prove', label: 'retry after wiring fix' },
    // Terminal fork: the run ends in exactly one of two outcomes.
    { a: 'prove', b: 'run_done', label: 'app started', weight: 20 },
    { a: 'prove', b: 'run_halt', label: 'from any stage', kind: 'abort' },
  ],
  legend: 'Badged stages can abort the whole run → RUN_HALT  ·  global LLM budget (checked first, everywhere) · per-slice retries · timeout · app never starts',
};

// ============================================ DETAILED — panel A (main pipeline)
const mainPanel = {
  clusters: [
    { id: 'c_boot', label: '1. BOOTSTRAP', pal: C.bootstrap },
    { id: 'c_slice', label: '2. SLICE PIPELINE (per migration unit)', pal: C.slice },
    { id: 'c_routes', label: '3. ROUTES (once, after all slices)', pal: C.routes },
    { id: 'c_bootrun', label: '4. BOOT LOOP (failure blocks the run)', pal: C.boot },
  ],
  nodes: [
    { id: 'setup', label: 'setup', sub: 'build JAR, setup.sh', pal: C.bootstrap, parent: 'c_boot' },
    { id: 'bootstrap_check', label: 'bootstrap_check', sub: 'scaffold present?', pal: C.bootstrap, parent: 'c_boot' },
    { id: 'bootstrap_agent', label: 'bootstrap_agent', sub: 'LLM scaffold (premium)', pal: C.bootstrap, parent: 'c_boot' },
    { id: 'bootstrap_verify', label: 'bootstrap_verify', sub: 're-check files', pal: C.bootstrap, parent: 'c_boot' },
    { id: 'inventory', label: 'inventory', sub: 'pre-flight scan + units', pal: C.inventory },
    { id: 'slice_router', label: 'slice_router', sub: 'next non-done unit', pal: C.slice, parent: 'c_slice' },
    { id: 'transform', label: 'transform', sub: 'migrate-app; skips gaps', pal: C.slice, parent: 'c_slice' },
    { id: 'slice_finalize', label: 'slice_finalize', sub: 'record slice status', pal: C.slice, parent: 'c_slice' },
    { id: 'CFIX_A', label: 'compile-fix subgraph', sub: '⇣ panel B', pal: C.cfix, parent: 'c_slice', w: 196, h: 64 },
    { id: 'CFIX_B', label: 'compile-fix subgraph', sub: '⇣ panel B (same one)', pal: C.cfix, parent: 'c_routes', w: 196, h: 64 },
    { id: 'routes', label: 'routes', sub: 'annotate @*Mapping gaps', pal: C.routes, parent: 'c_routes' },
    { id: 'routes_fix_prep', label: 'routes_fix_prep', sub: 'phase=fix_cycle', pal: C.routes, parent: 'c_routes' },
    { id: 'after_fix_cycle', label: 'after_fix_cycle', sub: 'phase→slice; hand back', pal: C.routes },
    { id: 'config_mapping', label: 'config_mapping', sub: 'map leftover HOCON keys', pal: C.cfg },
    { id: 'verify', label: 'verify', sub: 'layer-count check', pal: C.cfg },
    { id: 'boot_run', label: 'boot_run', sub: 'mvn spring-boot:run', pal: C.boot, parent: 'c_bootrun' },
    { id: 'runtime_wiring', label: 'runtime_wiring', sub: 'LLM fixes beans/config', pal: C.boot, parent: 'c_bootrun' },
    { id: 'run_done', label: 'run_done', sub: 'success · exit 0', pal: C.term_ok },
    { id: 'run_halt', label: 'run_halt', sub: 'abort · exit 4/5/6', pal: C.term_bad },
  ],
  edges: [
    { a: 'setup', b: 'bootstrap_check', label: 'ok', weight: 20 },
    { a: 'bootstrap_check', b: 'bootstrap_agent', label: 'missing' },
    { a: 'bootstrap_agent', b: 'bootstrap_verify' },
    { a: 'bootstrap_verify', b: 'bootstrap_agent', label: 'retry' },
    { a: 'bootstrap_check', b: 'inventory', label: 'already scaffolded', weight: 20 },
    { a: 'bootstrap_verify', b: 'inventory', label: 'scaffolded' },
    { a: 'inventory', b: 'slice_router', label: 'units found', weight: 20 },
    { a: 'slice_router', b: 'transform', label: 'next unit', weight: 20 },
    { a: 'transform', b: 'CFIX_A', label: 'enter', kind: 'enter', weight: 20 },
    { a: 'CFIX_A', b: 'slice_finalize', label: 'phase=slice', kind: 'exit', weight: 20 },
    { a: 'slice_finalize', b: 'slice_router', label: 'continue' },
    { a: 'slice_router', b: 'routes', label: 'all units done', weight: 20 },
    { a: 'routes', b: 'routes_fix_prep', label: 'verify edits' },
    { a: 'routes_fix_prep', b: 'CFIX_B', label: 're-enter', kind: 'enter', weight: 20 },
    { a: 'CFIX_B', b: 'after_fix_cycle', label: 'phase=fix_cycle', kind: 'exit', weight: 20 },
    { a: 'after_fix_cycle', b: 'config_mapping', label: 'return_to', weight: 20 },
    { a: 'routes', b: 'config_mapping', label: 'no routes file' },
    { a: 'config_mapping', b: 'verify', label: 'done', weight: 20 },
    { a: 'verify', b: 'boot_run', label: 'ok', weight: 20 },
    { a: 'boot_run', b: 'runtime_wiring', label: 'never started' },
    { a: 'runtime_wiring', b: 'boot_run', label: 're-check' },
    { a: 'boot_run', b: 'run_done', label: 'app started', weight: 20 },
    { a: 'routes', b: 'routes', label: 'more gaps' },
    { a: 'config_mapping', b: 'config_mapping', label: 'leftover keys' },
    { a: 'setup', b: 'run_halt', label: 'setup failed', kind: 'abort' },
    { a: 'bootstrap_verify', b: 'run_halt', label: 'attempts exhausted', kind: 'abort' },
    { a: 'inventory', b: 'run_halt', label: 'no slices', kind: 'abort' },
    { a: 'slice_finalize', b: 'run_halt', label: 'abort outcome', kind: 'abort' },
    { a: 'routes', b: 'run_halt', label: 'budget', kind: 'abort' },
    { a: 'after_fix_cycle', b: 'run_halt', label: 'budget_exhausted', kind: 'abort' },
    { a: 'config_mapping', b: 'run_halt', label: 'budget', kind: 'abort' },
    { a: 'verify', b: 'run_halt', label: 'slice failed', kind: 'abort' },
    { a: 'runtime_wiring', b: 'run_halt', label: 'budget', kind: 'abort' },
  ],
};

// ======================================= DETAILED — panel B (compile-fix subgraph)
const cfixPanel = {
  ranksep: 88,
  nodes: [
    { id: 'IN', label: 'from transform /\nroutes_fix_prep', sub: '', pal: C.cfix, w: 178, h: 60 },
    { id: 'compile', label: 'compile', sub: 'mvn compile (incremental)', pal: C.cfix },
    { id: 'human_gate', label: 'human_gate', sub: 'headless: pass-through', pal: C.cfix },
    { id: 'det_fix', label: 'det_fix', sub: 'deterministic fixers', pal: C.cfix },
    { id: 'cluster_n', label: 'cluster', sub: 'group remaining errors', pal: C.cfix },
    { id: 'guard', label: 'guard', sub: 'budget/retry/timeout/loop', pal: C.cfix },
    { id: 'agent', label: 'agent', sub: 'LLM compile-fix round', pal: C.cfix },
    { id: 'done', label: 'done', sub: 'compile succeeded', pal: C.cfix },
    { id: 'infra', label: 'infra', sub: 'infrastructure error', pal: C.cfix },
    { id: 'halt', label: 'halt', sub: 'gave up this slice', pal: C.cfix },
    { id: 'OUT', label: 'route_by_phase', sub: '→ slice_finalize | after_fix_cycle', pal: C.slice, w: 220, h: 64 },
  ],
  edges: [
    { a: 'IN', b: 'compile', kind: 'enter', weight: 20 },
    { a: 'compile', b: 'det_fix', label: 'errors', weight: 20 },
    { a: 'det_fix', b: 'compile', label: 'fixed → recompile' },
    { a: 'det_fix', b: 'cluster_n', label: 'no progress', weight: 20 },
    { a: 'cluster_n', b: 'guard', weight: 20 },
    { a: 'guard', b: 'agent', label: 'allowed', weight: 20 },
    { a: 'agent', b: 'compile', label: 'recompile' },
    { a: 'compile', b: 'human_gate', label: 'infra error' },
    { a: 'human_gate', b: 'compile', label: 'retry (interactive)' },
    { a: 'human_gate', b: 'infra', label: 'accept' },
    { a: 'compile', b: 'done', label: 'ok' },
    { a: 'guard', b: 'halt', label: 'budget/retries/timeout/loop' },
    { a: 'compile', b: 'halt', label: 'no llm' },
    { a: 'done', b: 'OUT', kind: 'exit' },
    { a: 'infra', b: 'OUT', kind: 'exit' },
    { a: 'halt', b: 'OUT', kind: 'exit' },
  ],
};

fs.writeFileSync('layout-overview.json', JSON.stringify(build(overview), null, 2));
const A = build(mainPanel), B = build(cfixPanel);
fs.writeFileSync('layout-detailed.json', JSON.stringify({ panels: [
  { title: 'A · MAIN PIPELINE — the shared compile-fix subgraph appears as a reference box (⇣ panel B) at each of its two entry points', layout: A },
  { title: 'B · COMPILE-FIX SUBGRAPH — shared: entered from transform (slice) and routes_fix_prep (fix-cycle)', layout: B },
] }, null, 2));
console.log('overview canvas', Math.round(build(overview).graph.width), 'x', Math.round(build(overview).graph.height));
console.log('panelA', A.nodes.length, 'nodes', Math.round(A.graph.width), 'x', Math.round(A.graph.height));
console.log('panelB', B.nodes.length, 'nodes', Math.round(B.graph.width), 'x', Math.round(B.graph.height));
