"""Render a single self-contained, offline HTML dashboard from the insights
bundle. No external libraries: charts are drawn as inline SVG by vanilla JS, so
the file opens in any browser with no network and can be archived for reference.
"""

from __future__ import annotations

import html
import json


def render(bundle: dict, path: str) -> None:
    data_json = json.dumps(bundle, ensure_ascii=False)
    doc = (_HEAD
           + "<script>\nconst DATA = " + data_json + ";\n</script>\n"
           + "<script>\n" + _JS + "\n</script>\n</body></html>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)


def render_combined(bundle: dict, path: str) -> None:
    """A standalone, printable page: the whole document, all tabs in creation
    order, every character colored by its author."""
    A = bundle["authors"]
    color = {i: a["color"] for i, a in enumerate(A)}
    legend = "".join(
        f'<span style="margin-right:14px"><span style="display:inline-block;width:11px;'
        f'height:11px;background:{a["color"]};border-radius:3px"></span> {html.escape(a["name"])}</span>'
        for a in A)
    tabs = sorted([t for t in bundle["tabs"] if t["id"] in bundle["colored"]],
                  key=lambda t: t.get("created_ts") or "")
    parts = []
    for t in tabs:
        spans = []
        for c in bundle["colored"][t["id"]]:
            col = color.get(c[1], "#888")
            ch = "<br>" if c[0] == "\n" else html.escape(c[0])
            spans.append(f'<span style="color:{col}">{ch}</span>')
        parts.append(f'<h2>{html.escape(t["title"])}</h2><div class="t">{"".join(spans)}</div>')
    doc = (f'<!doctype html><meta charset="utf-8"><title>All tabs — colored by author</title>'
           f'<body style="font:15px/1.7 Georgia,serif;max-width:860px;margin:36px auto;color:#111">'
           f'<h1>Full document — colored by author</h1>'
           f'<div style="margin:8px 0 20px">{legend}</div>'
           f'<style>.t{{white-space:pre-wrap;margin:0 0 26px}}h2{{margin:26px 0 6px;'
           f'border-bottom:1px solid #ddd;padding-bottom:3px}}</style>'
           f'{"".join(parts)}</body>')
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)


_HEAD = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Document Revision Analytics</title>
<style>
:root{--bg:#0b0f17;--panel:#141a26;--ink:#e7edf6;--mut:#8a97ab;--line:#222c3c;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial}
header{padding:24px 28px;border-bottom:1px solid var(--line);background:linear-gradient(180deg,#111726,#0b0f17)}
h1{margin:0 0 4px;font-size:20px}
h2{font-size:15px;letter-spacing:.04em;text-transform:uppercase;color:var(--mut);margin:0 0 14px}
.sub{color:var(--mut)}
nav{position:sticky;top:0;z-index:5;display:flex;gap:6px;flex-wrap:wrap;padding:10px 28px;background:#0d121c;border-bottom:1px solid var(--line)}
nav a{color:var(--mut);text-decoration:none;padding:6px 10px;border-radius:6px;font-size:13px}
nav a:hover{background:var(--panel);color:var(--ink)}
section{padding:24px 28px;border-bottom:1px solid var(--line)}
.grid{display:grid;gap:18px}
.cards{grid-template-columns:repeat(auto-fit,minmax(190px,1fr))}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}
.big{font-size:26px;font-weight:700}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px}
.two{grid-template-columns:1fr 1fr}
.three{grid-template-columns:1fr 1fr 1fr}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{color:var(--mut);font-weight:600}
.bar{height:14px;border-radius:3px;display:inline-block}
.swatch{display:inline-block;width:11px;height:11px;border-radius:3px;margin-right:6px;vertical-align:middle}
.pill{display:inline-block;padding:2px 8px;border-radius:20px;font-size:12px;background:#1d2636}
.doc{font:15px/1.7 Georgia,serif;background:#0e1420;border:1px solid var(--line);border-radius:10px;padding:18px;max-height:520px;overflow:auto;white-space:pre-wrap}
.mut{color:var(--mut)} .right{text-align:right} .mono{font-family:ui-monospace,Menlo,monospace}
select,input[type=range]{accent-color:#2563eb}
select{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:6px 8px}
.del{text-decoration:line-through;opacity:.65}
.warn{color:#f59e0b}
.legend{display:flex;gap:14px;flex-wrap:wrap;margin:6px 0 14px}
.kbd{font-family:ui-monospace,monospace;background:#1d2636;padding:1px 6px;border-radius:5px}
</style></head><body>
<header><h1>Document Revision Analytics</h1><div class="sub" id="hdr"></div></header>
<nav>
 <a href="#summary">Summary</a><a href="#overview">Overview</a><a href="#tabs">Tabs</a>
 <a href="#timeline">Timeline</a><a href="#activity">Activity</a><a href="#war">Deletions</a>
 <a href="#deleted">Deleted text</a><a href="#pastes">Pastes</a>
 <a href="#structure">Links &amp; structure</a><a href="#text">Colored text</a>
 <a href="#combined">All-tabs text</a><a href="#playback">Playback</a>
</nav>
<main id="main"></main>
"""

_JS = r"""
const A = DATA.authors, AC = i => (A[i]?A[i].color:'#888'), AN = i => (A[i]?A[i].name:'?');
const esc = s => (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const fmtDate = s => s? s.replace('T',' ').replace(/\..*/,'').replace('+00:00','')+' UTC':'';
const el = (h)=>{const d=document.createElement('div');d.innerHTML=h;return d.firstElementChild;};
const main = document.getElementById('main');
function sec(id,title,inner){const s=document.createElement('section');s.id=id;
  s.innerHTML='<h2>'+title+'</h2>'+inner;main.appendChild(s);return s;}

document.getElementById('hdr').innerHTML =
  'Document <span class="mono">'+esc(DATA.doc_id)+'</span> &middot; '+DATA.total_revs+
  ' revisions &middot; '+DATA.total_chars.toLocaleString()+' surviving chars across '+
  DATA.tabs.length+' tabs &middot; generated '+fmtDate(DATA.generated);

function legend(){return '<div class="legend">'+A.map((a,i)=>
  '<span><span class="swatch" style="background:'+a.color+'"></span>'+esc(a.name)+'</span>').join('')+'</div>';}

/* ---------- Executive summary ---------- */
(function(){
  const X=DATA.executive; if(!X) return;
  const tot=A.reduce((s,a)=>s+a.surviving,0)||1;
  let own=X.ownership.map(o=>'<div style="margin:6px 0"><div class="mut" style="display:flex;justify-content:space-between"><span><span class="swatch" style="background:'+o.color+'"></span>'+esc(o.name)+'</span><span>'+o.pct+'%</span></div><div class="bar" style="width:'+o.pct+'%;background:'+o.color+'"></div></div>').join('');
  const li=arr=>'<ul style="margin:6px 0 0;padding-left:18px">'+arr.map(s=>'<li>'+esc(s)+'</li>').join('')+'</ul>';
  sec('summary','Executive summary',
    '<div class="panel" style="border-color:#2a3a55"><div style="font-size:16px;font-weight:600;margin-bottom:6px">'+esc(X.headline)+'</div>'+
    '<div class="mut">'+X.facts.map(esc).join(' &nbsp;·&nbsp; ')+'</div></div>'+
    '<div class="grid three" style="margin-top:16px">'+
    '<div class="panel"><b>Final-text ownership</b>'+own+'</div>'+
    '<div class="panel"><b>Authorship style</b>'+li(X.authorship_style)+'</div>'+
    '<div class="panel"><b>Deletions between authors</b>'+li(X.deletions.length?X.deletions:['No cross-author deletions'])+'</div>'+
    '</div>'+
    '<div class="panel" style="margin-top:16px"><b>Tab ownership</b><div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px">'+
    X.tab_owners.map(t=>'<span class="pill">'+esc(t.tab)+' → <b>'+esc(t.owner)+'</b> '+t.pct+'%</span>').join('')+'</div></div>');
})();

/* ---------- Overview ---------- */
(function(){
  const tot = A.reduce((s,a)=>s+a.surviving,0)||1;
  let cards = '<div class="grid cards">'+A.map(a=>
    '<div class="card"><div><span class="swatch" style="background:'+a.color+'"></span><b>'+esc(a.name)+'</b></div>'+
    '<div class="big">'+Math.round(100*a.surviving/tot)+'%</div>'+
    '<div class="mut">'+a.surviving.toLocaleString()+' surviving chars</div>'+
    '<div class="mut">'+a.inserted.toLocaleString()+' inserted &middot; '+a.deleted.toLocaleString()+' deleted</div>'+
    '<div class="mut">survival '+Math.round(a.survival_rate*100)+'% &middot; '+a.edits.toLocaleString()+' edits</div>'+
    '<div class="mut">typed '+a.typed.toLocaleString()+' &middot; pasted '+a.pasted.toLocaleString()+'</div>'+
    '<div class="mut">'+a.active_days+' active days</div></div>').join('')+'</div>';
  const s = sec('overview','Document-wide ownership',
    '<div class="grid two"><div class="panel"><div id="donut"></div></div>'+
    '<div class="panel"><b>Final-text ownership</b><div id="ownbars" style="margin-top:10px"></div></div></div>'+cards);
  // donut
  let ang=-Math.PI/2, R=90, cx=110, cy=110, paths='';
  A.forEach(a=>{const frac=a.surviving/tot; if(frac<=0)return; const a2=ang+frac*2*Math.PI;
    const x1=cx+R*Math.cos(ang),y1=cy+R*Math.sin(ang),x2=cx+R*Math.cos(a2),y2=cy+R*Math.sin(a2);
    const large=frac>0.5?1:0;
    paths+='<path d="M'+cx+' '+cy+' L'+x1+' '+y1+' A'+R+' '+R+' 0 '+large+' 1 '+x2+' '+y2+' Z" fill="'+a.color+'" stroke="#0b0f17" stroke-width="2"/>';
    ang=a2;});
  document.getElementById('donut').innerHTML='<svg width="220" height="220">'+paths+
    '<circle cx="110" cy="110" r="46" fill="#141a26"/></svg>';
  // ownership bars
  document.getElementById('ownbars').innerHTML = A.map(a=>{const p=100*a.surviving/tot;
    return '<div style="margin:8px 0"><div class="mut" style="display:flex;justify-content:space-between"><span>'+
    esc(a.name)+'</span><span>'+a.surviving.toLocaleString()+'</span></div>'+
    '<div class="bar" style="width:'+p+'%;background:'+a.color+'"></div></div>';}).join('');
})();

/* ---------- Tabs ---------- */
(function(){
  let rows = DATA.tabs.map(t=>{
    const tot=t.chars||1; let seg='';
    Object.entries(t.by_author).sort((a,b)=>b[1]-a[1]).forEach(([i,n])=>{
      seg+='<span class="bar" title="'+esc(AN(i))+': '+n+'" style="width:'+(100*n/tot)+'%;background:'+AC(i)+'"></span>';});
    const dom=Object.entries(t.by_author).sort((a,b)=>b[1]-a[1])[0];
    return '<tr><td><b>'+esc(t.title)+'</b><div class="mut mono">'+esc(t.id)+'</div></td>'+
      '<td class="right">'+t.chars.toLocaleString()+'</td>'+
      '<td style="min-width:240px"><div style="display:flex;height:14px;border-radius:3px;overflow:hidden">'+seg+'</div></td>'+
      '<td>'+(dom?('<span class="swatch" style="background:'+AC(dom[0])+'"></span>'+esc(AN(dom[0]))+' '+Math.round(100*dom[1]/tot)+'%'):'')+'</td>'+
      '<td>'+(t.created_by!=null?esc(AN(t.created_by)):'?')+'<div class="mut">'+fmtDate(t.created_ts)+'</div></td></tr>';}).join('');
  sec('tabs','Per-tab authorship', legend()+
    '<div class="panel"><table><tr><th>Tab</th><th class="right">Chars</th><th>Ownership</th><th>Dominant author</th><th>Created by</th></tr>'+rows+'</table></div>');
})();

/* ---------- Timeline (cumulative) ---------- */
(function(){
  const C=DATA.timeline.cumulative; if(!C.length){sec('timeline','Timeline','<div class="panel mut">No timestamped data.</div>');return;}
  const W=920,H=320,pad=40;
  let maxY=0; C.forEach(p=>A.forEach((a,i)=>{maxY=Math.max(maxY,p.by[i]||0)}));
  maxY=maxY||1;
  const X=k=>pad+(W-2*pad)*k/(C.length-1||1), Y=v=>H-pad-(H-2*pad)*v/maxY;
  let lines='';
  A.forEach((a,i)=>{let d='';C.forEach((p,k)=>{d+=(k?'L':'M')+X(k).toFixed(1)+' '+Y(p.by[i]||0).toFixed(1)+' ';});
    lines+='<path d="'+d+'" fill="none" stroke="'+a.color+'" stroke-width="2.5"/>';});
  let axis='<line x1="'+pad+'" y1="'+(H-pad)+'" x2="'+(W-pad)+'" y2="'+(H-pad)+'" stroke="#2a3547"/>'+
    '<text x="'+pad+'" y="20" fill="#8a97ab" font-size="11">cumulative characters inserted &middot; '+
    fmtDate(C[0].ts)+' → '+fmtDate(C[C.length-1].ts)+'</text>'+
    '<text x="'+pad+'" y="'+(pad-6)+'" fill="#8a97ab" font-size="11">'+maxY.toLocaleString()+'</text>';
  sec('timeline','Who built the document, and when', legend()+
    '<div class="panel"><svg width="'+W+'" height="'+H+'" style="max-width:100%">'+axis+lines+'</svg></div>');
})();

/* ---------- Activity heatmap (hour of day) ---------- */
(function(){
  const hh=DATA.timeline.hour; let max=1;
  Object.values(hh).forEach(arr=>arr.forEach(v=>max=Math.max(max,v)));
  let rows=A.map((a,i)=>{const arr=hh[i]||[];
    let cells=arr.map(v=>{const t=v/max;const bg=t?('rgba(37,99,235,'+(0.15+0.85*t).toFixed(2)+')'):'#0e1420';
      return '<td title="'+v+' edits" style="background:'+bg+';width:26px;height:20px;border:1px solid #0b0f17"></td>';}).join('');
    return '<tr><td style="white-space:nowrap"><span class="swatch" style="background:'+a.color+'"></span>'+esc(a.name)+'</td>'+cells+'</tr>';}).join('');
  let hdr='<tr><th></th>'+Array.from({length:24},(_,h)=>'<th style="font-size:10px;text-align:center">'+h+'</th>').join('')+'</tr>';
  sec('activity','Activity by hour of day (UTC)', '<div class="panel" style="overflow:auto"><table>'+hdr+rows+'</table></div>');
})();

/* ---------- Deletions matrix ---------- */
(function(){
  const M=DATA.deletions.matrix;
  let rows=A.map((o,oi)=>{let tds=A.map((d,di)=>{const v=(M[oi]&&M[oi][di])||0;
      const t=v?Math.min(1,v/2000):0;const bg=v?('rgba(220,38,38,'+(0.15+0.8*t).toFixed(2)+')'):'#0e1420';
      return '<td class="right" style="background:'+bg+'">'+(v?v.toLocaleString():'')+'</td>';}).join('');
    return '<tr><td><span class="swatch" style="background:'+o.color+'"></span>'+esc(o.name)+'</td>'+tds+'</tr>';}).join('');
  let hdr='<tr><th>author ↓ deleted by →</th>'+A.map(a=>'<th class="right">'+esc(a.name)+'</th>').join('')+'</tr>';
  sec('war','Deletions between authors', '<div class="panel"><div class="mut" style="margin-bottom:8px">Rows = original author of the text; columns = who deleted it. Counts are characters.</div><table>'+hdr+rows+'</table></div>');
})();

/* ---------- Deleted passages ---------- */
(function(){
  const P=DATA.deletions.passages;
  let rows=P.map(p=>'<tr><td>'+esc(AN(p.orig_author))+'</td><td>'+esc(AN(p.del_author))+'</td>'+
    '<td>'+fmtDate(p.del_ts)+'</td><td class="right">'+p.len+'</td>'+
    '<td><span class="del">'+esc(p.text)+(p.len>600?'…':'')+'</span></td></tr>').join('');
  sec('deleted','Deleted text ('+P.length+' passages ≥ 40 chars)',
    '<div class="panel" style="max-height:560px;overflow:auto"><table><tr><th>Original author</th><th>Deleted by</th><th>When</th><th class="right">Len</th><th>Text</th></tr>'+
    (rows||'<tr><td class="mut">none</td></tr>')+'</table></div>');
})();

/* ---------- Pastes ---------- */
(function(){
  const P=DATA.pastes;
  let rows=P.map(p=>'<tr><td>'+esc(AN(p.author))+'</td><td>'+fmtDate(p.ts)+'</td>'+
    '<td class="right">'+p.size.toLocaleString()+'</td><td class="mut">'+esc(p.preview)+'…</td></tr>').join('');
  sec('pastes','Large inserts / likely pastes ('+P.length+')',
    '<div class="panel" style="max-height:480px;overflow:auto"><table><tr><th>Author</th><th>When</th><th class="right">Chars</th><th>Preview</th></tr>'+
    (rows||'<tr><td class="mut">none</td></tr>')+'</table></div>');
})();

/* ---------- Links & structure ---------- */
(function(){
  const S=DATA.structure;
  const countRow=(label,obj)=>'<tr><td>'+label+'</td>'+A.map((a,i)=>'<td class="right">'+((obj[i]||0))+'</td>').join('')+'</tr>';
  let counts='<table><tr><th>Element</th>'+A.map(a=>'<th class="right">'+esc(a.name)+'</th>').join('')+'</tr>'+
    countRow('Hyperlinks',S.links_by_author)+countRow('Images/objects',S.images_by_author)+
    countRow('Lists',S.lists_by_author)+countRow('Tables',S.tables_by_author)+
    countRow('Headings',S.headings_by_author)+countRow('Comment anchors',S.comments_by_author)+'</table>';
  let links=S.links.slice(0,200).map(l=>'<tr><td>'+esc(AN(l.author))+'</td><td class="mono" style="word-break:break-all">'+esc(l.url)+'</td><td>'+fmtDate(l.ts)+'</td></tr>').join('');
  sec('structure','Links, citations &amp; structure', '<div class="grid two">'+
    '<div class="panel"><b>Who added what</b>'+counts+'</div>'+
    '<div class="panel" style="max-height:420px;overflow:auto"><b>Hyperlinks added ('+S.links.length+')</b><table><tr><th>By</th><th>URL</th><th>When</th></tr>'+(links||'<tr><td class="mut">none</td></tr>')+'</table></div></div>');
})();

/* ---------- Colored text ---------- */
(function(){
  const tabs=Object.keys(DATA.colored);
  let opts=DATA.tabs.filter(t=>DATA.colored[t.id]).map(t=>'<option value="'+t.id+'">'+esc(t.title)+'</option>').join('');
  const s=sec('text','Per-character authorship', legend()+
    '<div class="panel"><div style="margin-bottom:10px">Tab: <select id="ctab">'+opts+'</select> <span class="mut">hover a character for author &amp; time</span></div><div class="doc" id="ctext"></div></div>');
  function draw(id){const cells=DATA.colored[id]||[];
    document.getElementById('ctext').innerHTML=cells.map(c=>{const ch=c[0]==='\n'?'\n':esc(c[0]);
      return '<span style="color:'+AC(c[1])+'" title="'+esc(AN(c[1]))+' · '+fmtDate(new Date(c[2]).toISOString())+'">'+ch+'</span>';}).join('');}
  document.getElementById('ctab').onchange=e=>draw(e.target.value);
  if(opts) draw(DATA.tabs.find(t=>DATA.colored[t.id]).id);
})();

/* ---------- Combined all-tabs colored ---------- */
(function(){
  const ordered=DATA.tabs.filter(t=>DATA.colored[t.id])
    .slice().sort((a,b)=>(a.created_ts||'').localeCompare(b.created_ts||''));
  function colorize(cells){return cells.map(c=>{const ch=c[0]==='\n'?'\n':esc(c[0]);
    return '<span style="color:'+AC(c[1])+'" title="'+esc(AN(c[1]))+' · '+fmtDate(new Date(c[2]).toISOString())+'">'+ch+'</span>';}).join('');}
  let body=ordered.map(t=>{
    const tot=t.chars||1;
    let seg=Object.entries(t.by_author).sort((a,b)=>b[1]-a[1]).map(([i,n])=>
      '<span class="bar" style="width:'+(100*n/tot)+'%;background:'+AC(i)+'"></span>').join('');
    return '<h3 style="margin:22px 0 4px">'+esc(t.title)+'</h3>'+
      '<div style="display:flex;height:8px;border-radius:3px;overflow:hidden;margin-bottom:10px">'+seg+'</div>'+
      '<div>'+colorize(DATA.colored[t.id])+'</div>';}).join('');
  sec('combined','Full document — all tabs, colored by author', legend()+
    '<div class="mut" style="margin-bottom:10px">Tabs in creation order. Each character is tinted by its author; hover for author &amp; time. This is the whole reconstructed document on one page.</div>'+
    '<div class="doc" style="max-height:720px">'+body+'</div>');
})();

/* ---------- Playback ---------- */
(function(){
  let opts=Object.keys(DATA.playback).map(id=>{const t=DATA.tabs.find(x=>x.id===id);
    return '<option value="'+id+'">'+esc(t?t.title:id)+'</option>';}).join('');
  sec('playback','Playback — watch the document being written',
    '<div class="panel"><div style="margin-bottom:10px">Tab: <select id="ptab"></select> '+
    '<button id="pplay">▶ Play</button> <span class="mut" id="pinfo"></span></div>'+
    '<input id="pslider" type="range" min="0" value="0" style="width:100%"><div class="doc" id="ptext"></div></div>');
  const sel=document.getElementById('ptab'); sel.innerHTML=opts;
  const slider=document.getElementById('pslider'), out=document.getElementById('ptext'),
        info=document.getElementById('pinfo'), playBtn=document.getElementById('pplay');
  let events=[], timer=null;
  function load(id){events=DATA.playback[id]||[]; slider.max=events.length; slider.value=events.length; render();}
  function render(){const n=+slider.value; let cells=[];
    for(let k=0;k<n;k++){const e=events[k];
      if(e[0]===1){const pos=Math.max(0,Math.min((e[1]||1)-1,cells.length));const ins=[];
        for(const ch of e[2]) ins.push([ch,e[3]]); cells.splice(pos,0,...ins);}
      else{const lo=Math.max(0,(e[1]||1)-1),hi=Math.min(cells.length-1,(e[2]||1)-1); if(hi>=lo) cells.splice(lo,hi-lo+1);}}
    out.innerHTML=cells.map(c=>'<span style="color:'+AC(c[1])+'">'+(c[0]==='\n'?'\n':esc(c[0]))+'</span>').join('');
    const e=events[n-1]; info.textContent='rev '+n+'/'+events.length+(e?' · '+AN(e[3])+' · '+fmtDate(new Date(e[4]).toISOString()):'');}
  slider.oninput=render;
  playBtn.onclick=()=>{ if(timer){clearInterval(timer);timer=null;playBtn.textContent='▶ Play';return;}
    if(+slider.value>=events.length) slider.value=0; playBtn.textContent='⏸ Pause';
    timer=setInterval(()=>{ if(+slider.value>=events.length){clearInterval(timer);timer=null;playBtn.textContent='▶ Play';return;}
      slider.value=+slider.value+Math.max(1,Math.floor(events.length/400)); render();},40);};
  sel.onchange=e=>load(e.target.value);
  if(opts) load(sel.value);
})();
"""
