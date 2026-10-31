const params = new URLSearchParams(location.search);
const API = (params.get("api") || localStorage.getItem("unai-brain-api") || "http://127.0.0.1:8787").replace(/\/$/, "");
const el = id => document.getElementById(id);
let graphData = {nodes:[],edges:[]};

async function api(path) {
  const response = await fetch(API + path);
  if (!response.ok) throw new Error("Brain API returned " + response.status);
  return response.json();
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function drawGraph(data) {
  graphData = data;
  const nodes = data.nodes || [], edges = data.edges || [];
  el("counts").textContent = nodes.length + " NOTES · " + edges.length + " LINKS";
  if (!nodes.length) { el("graph").innerHTML = '<div class="empty">No indexed notes yet. Add Markdown files to your vault and run the Brain indexer.</div>'; return; }
  const width=760,height=250,cx=width/2,cy=height/2,rad=Math.min(95,Math.max(34,nodes.length*12));
  const positions = nodes.map((n,i)=>({n,x:cx+Math.cos(2*Math.PI*i/nodes.length)*rad,y:cy+Math.sin(2*Math.PI*i/nodes.length)*rad}));
  const byId = new Map(positions.map(p=>[p.n.id,p]));
  const lines = edges.map(e=>{const a=byId.get(e.source),b=byId.get(e.target);return a&&b?'<line class="edge" x1="'+a.x+'" y1="'+a.y+'" x2="'+b.x+'" y2="'+b.y+'"/>':"";}).join("");
  const dots = positions.map(p=>'<g class="node" data-id="'+escapeHtml(p.n.id)+'"><circle cx="'+p.x+'" cy="'+p.y+'" r="5"/><text x="'+p.x+'" y="'+(p.y+19)+'">'+escapeHtml((p.n.label||p.n.title).slice(0,20))+'</text></g>').join("");
  el("graph").innerHTML='<svg viewBox="0 0 '+width+' '+height+'" role="img" aria-label="Knowledge graph">'+lines+dots+'</svg>';
  el("graph").querySelectorAll(".node").forEach(n=>n.addEventListener("click",()=>readNote(n.dataset.id)));
}
function renderResults(rows,title) {
  el("listTitle").textContent=title;
  el("results").innerHTML=rows.length?rows.map(r=>'<article class="result" data-id="'+escapeHtml(r.slug||r.id)+'"><strong>'+escapeHtml(r.title)+'</strong><p>'+escapeHtml(r.excerpt||"Open note to see details.")+'</p><div class="tags">'+(r.tags||JSON.parse(r.tags_json||"[]")).slice(0,5).map(t=>'<span class="tag">#'+escapeHtml(t)+'</span>').join("")+'</div></article>').join(""):'<div class="empty">No matching notes.</div>';
  el("results").querySelectorAll(".result").forEach(n=>n.addEventListener("click",()=>readNote(n.dataset.id)));
}
async function readNote(id) {
  try {
    const n=await api("/api/notes/"+encodeURIComponent(id));
    el("reader").innerHTML='<p class="eyebrow">NOTE</p><h2>'+escapeHtml(n.title)+'</h2><p class="path">'+escapeHtml(n.path||n.slug||n.id)+'</p><div class="tags">'+(n.tags||[]).map(t=>'<span class="tag">#'+escapeHtml(t)+'</span>').join("")+'</div><hr><div class="body">'+escapeHtml(n.body)+'</div><p class="eyebrow">BACKLINKS</p>'+((n.backlinks||[]).map(b=>'<a class="backlink" href="#" data-id="'+escapeHtml(b.path||b.slug||b.id)+'">'+escapeHtml(b.title||b.slug)+'</a>').join("")||'<p class="path">No backlinks yet.</p>');
    el("reader").querySelectorAll(".backlink").forEach(a=>a.addEventListener("click",e=>{e.preventDefault();readNote(a.dataset.id);}));
  } catch(e) { el("reader").textContent=e.message; }
}
async function search(q) {
  const data=await api("/api/search?q="+encodeURIComponent(q)+"&limit=20");
  renderResults(data.results||[],"Search results");
}
async function boot() {
  try {
    const health=await api("/api/health");
    el("health").innerHTML='<span class="dot ok"></span> Connected · '+(health.notes||0)+' notes';
    const graph=await api("/api/graph"); drawGraph(graph);
    renderResults(graph.nodes||[],"All notes");
  } catch(e) {
    el("health").innerHTML='<span class="dot"></span> API unavailable';
    el("graph").innerHTML='<div class="empty">'+escapeHtml(e.message)+'<br>Set ?api=https://your-worker.workers.dev to connect.</div>';
  }
}
el("query").addEventListener("keydown",e=>{if(e.key==="Enter")search(e.currentTarget.value).catch(err=>{el("results").textContent=err.message;});});
el("allNotes").addEventListener("click",()=>renderResults(graphData.nodes||[],"All notes"));
el("orphans").addEventListener("click",()=>renderResults((graphData.nodes||[]).filter(n=>!(graphData.edges||[]).some(edge=>edge.source===n.id)), "Unlinked notes"));
boot();
