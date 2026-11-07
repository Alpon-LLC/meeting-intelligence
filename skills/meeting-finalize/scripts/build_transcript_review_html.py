#!/usr/bin/env python3
"""Build a private browser-based transcript review player from the TSV worksheet."""
from __future__ import annotations

import argparse
import csv
import html
import json
import os
import tempfile
from pathlib import Path


def load_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames:
            raise ValueError("review worksheet has no header")
        required = {"audio_start", "audio_end", "speaker", "text", "human_text"}
        missing = required - set(reader.fieldnames)
        if missing:
            raise ValueError(f"review worksheet is missing: {', '.join(sorted(missing))}")
        return list(reader.fieldnames), list(reader)


def render_html(rows: list[dict[str, str]], fields: list[str], audio_name: str, title: str) -> str:
    payload = json.dumps({"rows": rows, "fields": fields}, ensure_ascii=False, separators=(",", ":"))
    payload = payload.replace("</", "<\\/")
    safe_title = html.escape(title)
    safe_audio = html.escape(audio_name, quote=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{safe_title}</title>
<style>
:root{{--bg:#111310;--panel:#1b1e19;--line:#343a31;--text:#f2f3ed;--muted:#a6ada0;--accent:#d8ff5f;--high:#ff7a70;--medium:#ffc857}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.45 ui-sans-serif,system-ui,-apple-system,sans-serif}}
header{{position:sticky;top:0;z-index:5;background:rgba(17,19,16,.97);border-bottom:1px solid var(--line);padding:16px 22px}}
h1{{font-size:18px;margin:0 0 10px}} audio{{width:100%;height:38px}} .controls{{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}}
button,select,input{{background:var(--panel);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:8px 10px}}
button{{cursor:pointer}} button:hover{{border-color:var(--accent)}} .status{{color:var(--muted);margin-left:auto;padding:8px 0}}
#current{{font-size:22px;line-height:1.35;margin:16px 0 0;min-height:60px}} main{{max-width:1180px;margin:auto;padding:18px}}
.row{{display:grid;grid-template-columns:150px minmax(0,1fr);gap:16px;padding:16px 10px;border-bottom:1px solid var(--line)}}
.row.active{{background:#252b20;outline:1px solid #52623a}} .meta{{color:var(--muted);font-size:13px}} .speaker{{color:var(--accent);font-weight:700}}
.badge{{display:inline-block;margin-top:7px;padding:2px 6px;border-radius:4px;border:1px solid var(--line);font-size:11px;text-transform:uppercase}}
.badge.high{{color:var(--high);border-color:var(--high)}} .badge.medium{{color:var(--medium);border-color:var(--medium)}}
.original{{color:#c3c8bb;margin-bottom:8px}} textarea{{width:100%;min-height:64px;resize:vertical;background:#151713;color:var(--text);border:1px solid var(--line);border-radius:5px;padding:9px}}
.edit-meta{{display:flex;gap:8px;margin-top:8px}} .edit-meta input{{min-width:180px}} .seek{{width:100%;text-align:left;margin-bottom:8px}}
@media(max-width:700px){{.row{{grid-template-columns:1fr}} #current{{font-size:18px}}}}
</style></head><body>
<header><h1>{safe_title}</h1><audio id="audio" controls preload="metadata" src="{safe_audio}"></audio>
<div class="controls"><select id="priority"><option value="all">All rows</option><option value="high">High priority</option><option value="medium+" selected>High + medium</option><option value="normal">Normal</option></select>
<select id="speaker"><option value="all">All speakers</option></select><select id="speed"><option>0.75</option><option selected>1</option><option>1.25</option><option>1.5</option><option>2</option></select>
<button id="save">Save draft locally</button><button id="download">Download reviewed TSV</button><span class="status" id="status"></span></div>
<div id="current">Press play or click a transcript row.</div></header><main id="rows"></main>
<script id="review-data" type="application/json">{payload}</script>
<script>
const data=JSON.parse(document.getElementById('review-data').textContent),audio=document.getElementById('audio'),root=document.getElementById('rows'),current=document.getElementById('current');
const storageKey='vexa-review:'+location.pathname; let rows=data.rows; const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const stamp=n=>{{n=Math.max(0,Number(n)||0);const h=Math.floor(n/3600),m=Math.floor(n%3600/60),s=Math.floor(n%60);return (h?h+':':'')+String(m).padStart(2,'0')+':'+String(s).padStart(2,'0')}};
try{{const draft=JSON.parse(localStorage.getItem(storageKey));if(draft&&draft.length===rows.length)rows=rows.map((r,i)=>({{...r,human_text:draft[i].human_text,human_speaker:draft[i].human_speaker,overlap:draft[i].overlap,reviewer_notes:draft[i].reviewer_notes}}))}}catch(e){{}}
const speakers=[...new Set(rows.map(r=>r.speaker))].sort(); speakers.forEach(s=>document.getElementById('speaker').insertAdjacentHTML('beforeend',`<option value="${{esc(s)}}">${{esc(s)}}</option>`));
function visible(r){{const p=document.getElementById('priority').value,s=document.getElementById('speaker').value;return (s==='all'||r.speaker===s)&&(p==='all'||r.review_priority===p||(p==='medium+'&&(r.review_priority==='high'||r.review_priority==='medium')))}}
function render(){{root.innerHTML='';rows.forEach((r,i)=>{{if(!visible(r))return;const el=document.createElement('section');el.className='row';el.dataset.index=i;el.innerHTML=`<div><button class="seek" data-seek="${{r.audio_start}}">${{stamp(r.audio_start)}}–${{stamp(r.audio_end)}}</button><div class="speaker">${{esc(r.speaker)}}</div><div class="meta">confidence ${{esc(r.mean_confidence||'n/a')}}</div><span class="badge ${{esc(r.review_priority)}}">${{esc(r.review_priority)}}</span><div class="meta">${{esc(r.review_reason||'')}}</div></div><div><div class="original">${{esc(r.text)}}</div><textarea data-field="human_text" data-index="${{i}}">${{esc(r.human_text)}}</textarea><div class="edit-meta"><input data-field="human_speaker" data-index="${{i}}" value="${{esc(r.human_speaker)}}" aria-label="Human speaker"><label><input type="checkbox" data-field="overlap" data-index="${{i}}" ${{String(r.overlap).toLowerCase()==='true'?'checked':''}}> overlap</label><input data-field="reviewer_notes" data-index="${{i}}" value="${{esc(r.reviewer_notes)}}" placeholder="Reviewer notes"></div></div>`;root.appendChild(el)}});status()}}
root.addEventListener('click',e=>{{const b=e.target.closest('[data-seek]');if(b){{audio.currentTime=Number(b.dataset.seek);audio.play()}}}});root.addEventListener('input',e=>{{const i=e.target.dataset.index,f=e.target.dataset.field;if(f)rows[i][f]=e.target.type==='checkbox'?String(e.target.checked):e.target.value}});
function status(msg){{document.getElementById('status').textContent=msg||`${{root.children.length}} of ${{rows.length}} rows shown`}};
function sync(){{const t=audio.currentTime,i=rows.findIndex(r=>t>=Number(r.audio_start)&&t<Number(r.audio_end));document.querySelectorAll('.row.active').forEach(x=>x.classList.remove('active'));if(i>=0){{current.textContent=`${{rows[i].speaker}}: ${{rows[i].human_text||rows[i].text}}`;const el=document.querySelector(`[data-index="${{i}}"].row`);if(el)el.classList.add('active')}}}} audio.addEventListener('timeupdate',sync);
document.getElementById('priority').onchange=render;document.getElementById('speaker').onchange=render;document.getElementById('speed').onchange=e=>audio.playbackRate=Number(e.target.value);
document.getElementById('save').onclick=()=>{{localStorage.setItem(storageKey,JSON.stringify(rows.map(r=>({{human_text:r.human_text,human_speaker:r.human_speaker,overlap:r.overlap,reviewer_notes:r.reviewer_notes}}))));status('Draft saved in this browser')}};
document.getElementById('download').onclick=()=>{{const q=v=>'"'+String(v??'').replace(/"/g,'""')+'"',lines=[data.fields.join('\\t'),...rows.map(r=>data.fields.map(f=>q(r[f])).join('\\t'))],blob=new Blob([lines.join('\\n')+'\\n'],{{type:'text/tab-separated-values;charset=utf-8'}}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='human-review-reviewed.tsv';a.click();URL.revokeObjectURL(a.href)}};render();
</script></body></html>"""


def build(review: Path, output: Path, audio_name: str, title: str) -> int:
    fields, rows = load_rows(review)
    document = render_html(rows, fields, audio_name, title)
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
        os.chmod(output, 0o600)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--audio", default="review-audio.opus")
    parser.add_argument("--title", default="Vexa transcript review")
    args = parser.parse_args()
    count = build(args.review, args.output, args.audio, args.title)
    print(json.dumps({"status": "ok", "rows": count, "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
