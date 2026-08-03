const state = { graph: null };

async function loadGraph() {
  const candidates = ["../dist/graph/graph.json", "/dist/graph/graph.json"];
  let lastError;
  for (const url of candidates) {
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
      return await response.json();
    } catch (error) { lastError = error; }
  }
  throw lastError;
}

function statusCard(label, value) {
  return `<div class="card"><strong>${escapeHtml(value)}</strong><br/>${escapeHtml(label)}</div>`;
}

function renderFitness(graph, visibleNodeIds) {
  const evaluations = graph.fitness?.evaluations || [];
  const relevant = evaluations.filter(item => item.status !== "NOT_APPLICABLE" && (visibleNodeIds.size === 0 || visibleNodeIds.has(item.targetId)));
  const count = status => evaluations.filter(item => item.status === status).length;
  document.querySelector("#fitness-summary").innerHTML = [
    statusCard("PASS", count("PASS")),
    statusCard("FAIL", count("FAIL")),
    statusCard("UNKNOWN/STALE", count("UNKNOWN") + count("STALE")),
    statusCard("WAIVED", count("WAIVED")),
  ].join("");

  const ranked = [...relevant].sort((a, b) => {
    const order = { FAIL: 0, ERROR: 1, UNKNOWN: 2, STALE: 3, WAIVED: 4, PASS: 5 };
    return (order[a.status] ?? 9) - (order[b.status] ?? 9) || a.fitnessFunctionId.localeCompare(b.fitnessFunctionId);
  });
  document.querySelector("#fitness-results").innerHTML = ranked.map(item => `
    <tr>
      <td><span class="status status-${escapeClass(item.status)}">${escapeHtml(item.status)}</span></td>
      <td><strong>${escapeHtml(item.fitnessFunctionId)}</strong><br/><small>${escapeHtml(item.fitnessFunctionName)}</small></td>
      <td><small>${escapeHtml(item.targetId)}</small></td>
      <td>${escapeHtml(item.severity)}</td>
      <td>${escapeHtml(item.enforcement)}</td>
      <td>${escapeHtml(item.message)}</td>
    </tr>`).join("") || `<tr><td colspan="6">Nenhuma avaliação aplicável aos filtros atuais.</td></tr>`;
}

function render() {
  const graph = state.graph;
  const query = document.querySelector("#search").value.trim().toLowerCase();
  const type = document.querySelector("#type-filter").value;
  const nodes = graph.nodes.filter(node => {
    const text = `${node.id} ${node.name} ${node.description}`.toLowerCase();
    return (!type || node.type === type) && (!query || text.includes(query));
  });
  const visible = new Set(nodes.map(n => n.id));
  const relationships = graph.relationships.filter(r => visible.has(r.source) || visible.has(r.target));

  document.querySelector("#summary").innerHTML = `
    <div class="card"><strong>${graph.nodes.length}</strong><br/>nós publicados</div>
    <div class="card"><strong>${graph.relationships.length}</strong><br/>relações publicadas</div>
    <div class="card"><strong>${nodes.length}</strong><br/>nós visíveis</div>
    <div class="card"><strong>${graph.contentHash?.slice(0, 12) || "n/a"}</strong><br/>snapshot</div>`;

  renderFitness(graph, visible);

  document.querySelector("#graph").innerHTML = nodes.map(node => `
    <article class="node node-${escapeClass(node.type)}">
      <h3>${escapeHtml(node.name)}</h3>
      <span class="badge">${escapeHtml(node.type)}</span>
      <span class="badge">${escapeHtml(node.architectureState)}</span>
      ${node.attributes?.severity ? `<span class="badge">${escapeHtml(node.attributes.severity)}</span>` : ""}
      <p>${escapeHtml(node.description)}</p>
      <small>${escapeHtml(node.id)}</small>
    </article>`).join("") || "<p>Nenhum nó corresponde ao filtro.</p>";

  document.querySelector("#relationships").innerHTML = relationships.map(rel => `
    <div class="relationship"><strong>${escapeHtml(rel.type)}</strong>: ${escapeHtml(rel.source)} → ${escapeHtml(rel.target)}</div>`).join("") || "<p>Nenhuma relação visível.</p>";
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[char]));
}

function escapeClass(value) {
  return String(value ?? "unknown").toLowerCase().replace(/[^a-z0-9-]/g, "-");
}

loadGraph().then(graph => {
  state.graph = graph;
  const types = [...new Set(graph.nodes.map(node => node.type))].sort();
  document.querySelector("#type-filter").innerHTML += types.map(t => `<option>${escapeHtml(t)}</option>`).join("");
  document.querySelector("#search").addEventListener("input", render);
  document.querySelector("#type-filter").addEventListener("change", render);
  render();
}).catch(error => {
  document.querySelector("main").innerHTML = `<div class="error">Não foi possível carregar o grafo. Execute <code>python3 scripts/harness.py publish</code> e sirva a raiz do projeto via HTTP.<br/>${escapeHtml(error.message)}</div>`;
});
