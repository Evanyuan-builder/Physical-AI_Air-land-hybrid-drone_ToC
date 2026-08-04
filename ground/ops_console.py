#!/usr/bin/env python3
"""路演操作台 —— 说一句话，看它怎么想的，看那块屏跟着动。

补的是队里那个手机控制页没有的两样：
  · 一个能对它说话的地方（麦克风 / 打字）
  · 一条「它怎么想的」日志：听懂成什么 → 从画面里挑了谁 → 打给板子什么 → 板子回了什么

**不改队里任何文件。** 视频直接嵌他们的 MJPEG 流，控制走他们的 POST /api/state。

跑：
    python3 ops_console.py                                  # 全默认（本地 rig）
    python3 ops_console.py --board http://192.168.4.1:8080 \
                           --stream http://192.168.4.1:8000/stream
然后开 http://localhost:8090

零依赖，纯标准库 —— 跟这个目录里其它东西一样。
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent import GroundAgent
from downlink import StateApiDownlink
from intents import Intent
from memory import PreferenceMemory
from perception import MockYoloSource
from telemetry import MockTelemetry

CONSOLE_PORT = 8090
MAX_BODY = 4096


PAGE = r"""<!doctype html>
<html lang="zh"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>两栖跟拍机 · 地面大脑</title>
<style>
:root{
  --bg:#f5f5f7; --surface:#fff; --hover:#fbfbfd;
  --text:#1d1d1f; --text-2:#6e6e73; --text-3:#86868b; --text-4:#aeaeb2;
  --faint:#d2d2d7; --hairline:rgba(0,0,0,0.07);
  --accent:#0071e3; --accent-link:#0066cc; --live:#30d158; --heat:#ff6b00; --alarm:#ff453a;
  --font:-apple-system,BlinkMacSystemFont,'SF Pro Text','SF Pro Display','Helvetica Neue','PingFang SC',sans-serif;
  --mono:ui-monospace,'SF Mono',Menlo,monospace;
  --r-pill:999px; --r-thumb:12px; --r-card:18px; --r-panel:22px;
  --sh-panel:0 1px 3px rgba(0,0,0,0.05),0 14px 40px rgba(0,0,0,0.05);
  --sh-cta:0 10px 26px rgba(0,113,227,0.2);
  /* 内置 easing 太软 —— 用强曲线 */
  --ease-out:cubic-bezier(0.23,1,0.32,1);
  --ease-in-out:cubic-bezier(0.77,0,0.175,1);
  --max:1280px; --pad-x:24px;
}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:var(--font);line-height:1.6;
     -webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale}
[hidden]{display:none!important}
button{font-family:inherit;cursor:pointer;border:none;background:none;color:inherit}

/* ══ 顶栏：唯一该用玻璃的地方（它压着滚动内容）══ */
header{position:sticky;top:0;z-index:50;
  background:rgba(245,245,247,0.72);
  backdrop-filter:saturate(180%) blur(20px);
  -webkit-backdrop-filter:saturate(180%) blur(20px);
  border-bottom:1px solid var(--hairline)}
.nav{max-width:var(--max);margin:0 auto;padding:0 var(--pad-x);height:58px;
  display:flex;align-items:center;justify-content:space-between;gap:16px}
.brand{font-size:15px;font-weight:650;letter-spacing:-0.015em}
.brand em{font-style:normal;color:var(--text-3);font-weight:450;margin-left:10px;letter-spacing:0}
.link{display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--text-2)}
.dot{width:7px;height:7px;border-radius:50%;background:var(--faint);flex:none;
  transition:background 240ms ease}
.dot.up{background:var(--live);animation:pulse 2.4s ease-out infinite}
.dot.down{background:var(--alarm)}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(48,209,88,.45)}70%{box-shadow:0 0 0 7px rgba(48,209,88,0)}100%{box-shadow:0 0 0 0 rgba(48,209,88,0)}}

main{max-width:var(--max);margin:0 auto;padding:clamp(20px,3vw,32px) var(--pad-x) 72px;
  display:grid;grid-template-columns:minmax(0,1.38fr) minmax(0,1fr);gap:clamp(16px,2vw,24px);
  align-items:start}
@media(max-width:960px){main{grid-template-columns:1fr}}

/* ══ 面板：同类的东西放一块白面板 + 发丝线，不要碎成一堆卡片 ══ */
.panel{background:var(--surface);border-radius:var(--r-panel);box-shadow:var(--sh-panel);overflow:hidden}
.panel+.panel{margin-top:clamp(16px,2vw,24px)}
.phead{display:flex;align-items:center;justify-content:space-between;gap:12px;
  padding:14px clamp(16px,2vw,22px)}
.phead+*{border-top:1px solid var(--hairline)}
.ptitle{font-size:11px;font-weight:650;letter-spacing:0.13em;color:var(--text-3);text-transform:uppercase}
.pnote{font-size:12px;color:var(--text-4);display:flex;align-items:center;gap:7px}

/* ══ 取景器 ══ */
.feed{position:relative;background:#0b0b0c;aspect-ratio:4/3;overflow:hidden}
.feed::after{content:'';position:absolute;inset:0;pointer-events:none;
  box-shadow:inset 0 0 0 1px rgba(255,255,255,0.07);
  outline:0 solid rgba(0,113,227,0);outline-offset:-2px;
  transition:outline-color 520ms ease-out,outline-width 520ms ease-out}
.feed.armed::after{outline:2px solid rgba(0,113,227,0.55)}
.feed img{width:100%;height:100%;object-fit:contain;display:block}
.feed .off{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:6px;color:var(--text-4);font-size:13px;text-align:center;padding:24px;line-height:1.7}

/* ══ 遥测 ══ */
.tele{display:grid;grid-template-columns:repeat(auto-fit,minmax(96px,1fr))}
.tele>div{padding:14px clamp(13px,1.6vw,20px)}
.tele>div+div{border-left:1px solid var(--hairline)}
.tk{font-size:10px;font-weight:650;letter-spacing:0.11em;color:var(--text-4);text-transform:uppercase}
.tv{margin-top:4px;font-size:21px;font-weight:600;letter-spacing:-0.025em;
  font-variant-numeric:tabular-nums;line-height:1.15;
  transition:color 260ms ease,opacity 180ms ease,filter 180ms ease}
.tv u{font-style:normal;text-decoration:none;font-size:12px;font-weight:500;
  color:var(--text-4);letter-spacing:0;margin-left:2px}
.tv.blink{opacity:.55;filter:blur(2.5px)}
.tv.on{color:var(--accent)}
.tv.warn{color:var(--heat)}
.tv.dim{color:var(--text-3)}

/* ══ 画面里有谁（并进同一块面板，不另起一块）══ */
.who{display:flex;flex-wrap:wrap;gap:7px;padding:14px clamp(16px,2vw,22px)}
.wchip{font-size:12.5px;color:var(--text-2);background:rgba(0,0,0,0.045);
  border-radius:var(--r-pill);padding:5px 12px;font-variant-numeric:tabular-nums;
  opacity:0;transform:translateY(5px);transition:opacity 260ms var(--ease-out),transform 260ms var(--ease-out)}
.wchip.in{opacity:1;transform:none}
.wchip.owner{color:var(--accent-link);background:rgba(0,113,227,0.08);font-weight:550}
.wchip.none{color:var(--text-4);background:none;padding-left:0}

/* ══ 说话 ══ */
.say{padding:clamp(15px,2vw,20px)}
.sayrow{display:flex;gap:10px;align-items:flex-end}
textarea{flex:1;min-height:48px;max-height:132px;resize:none;font-family:inherit;
  font-size:15.5px;line-height:1.5;color:var(--text);background:var(--bg);
  border:1px solid transparent;border-radius:var(--r-thumb);padding:13px 15px;outline:none;
  transition:border-color 200ms var(--ease-out),background 200ms var(--ease-out)}
textarea:focus{background:var(--surface);border-color:var(--accent)}
textarea::placeholder{color:var(--text-4)}
.mic,.send{transition:transform 160ms var(--ease-out),background 200ms ease,
  box-shadow 200ms ease,color 200ms ease,opacity 200ms ease}
.mic:active,.send:active{transform:scale(0.97)}
.mic{width:48px;height:48px;flex:none;border-radius:50%;background:rgba(0,0,0,0.05);
  display:flex;align-items:center;justify-content:center;color:var(--text-2)}
.mic.rec{background:var(--alarm);color:#fff;animation:rec 1.5s ease-out infinite}
.mic:disabled{opacity:.35;cursor:not-allowed}
@keyframes rec{0%{box-shadow:0 0 0 0 rgba(255,69,58,.4)}70%{box-shadow:0 0 0 11px rgba(255,69,58,0)}100%{box-shadow:0 0 0 0 rgba(255,69,58,0)}}
.send{height:48px;padding:0 22px;flex:none;border-radius:var(--r-pill);background:var(--accent);
  color:#fff;font-size:15px;font-weight:600;box-shadow:var(--sh-cta)}
.send span{display:block;transition:filter 200ms ease,opacity 200ms ease}
.send.busy span{filter:blur(2.5px);opacity:.6}
.send:disabled{box-shadow:none;cursor:default}
.hints{margin-top:12px;display:flex;flex-wrap:wrap;gap:7px}
.hint{font-size:12.5px;color:var(--text-2);background:rgba(0,0,0,0.05);border-radius:var(--r-pill);
  padding:6px 13px;transition:background 180ms ease,color 180ms ease,transform 160ms var(--ease-out)}
.hint:active{transform:scale(0.97)}
.note{margin-top:12px;font-size:11.5px;color:var(--text-4);line-height:1.6}

/* ══ 决策日志 ══ */
.log{max-height:min(60vh,640px);overflow-y:auto;overscroll-behavior:contain}
.entry{padding:16px clamp(16px,2vw,22px);
  opacity:0;transform:translateY(10px);filter:blur(5px);
  transition:opacity 300ms var(--ease-out),transform 300ms var(--ease-out),filter 300ms var(--ease-out)}
.entry.in{opacity:1;transform:none;filter:blur(0)}
.entry+.entry{border-top:1px solid var(--hairline)}
.heard{font-size:18px;font-weight:600;letter-spacing:-0.022em;line-height:1.4;
  display:flex;align-items:baseline;gap:9px;flex-wrap:wrap}
.rows{margin-top:9px;display:grid;grid-template-columns:auto 1fr;gap:4px 11px;align-items:baseline}
.rk{font-size:10px;font-weight:650;letter-spacing:0.09em;color:var(--text-4);
  text-transform:uppercase;padding-top:3px;white-space:nowrap}
.rv{font-size:13.5px;color:var(--text-2);line-height:1.55;min-width:0}
.rv b{color:var(--text);font-weight:600}
.wire{grid-column:1/-1;font-family:var(--mono);font-size:11.5px;color:var(--text-2);
  background:var(--bg);border-radius:9px;padding:9px 11px;margin-top:5px;
  overflow-x:auto;white-space:pre;line-height:1.5}
.tag{font-size:11px;font-weight:650;border-radius:var(--r-pill);padding:3px 10px;
  letter-spacing:0.02em;flex:none}
.tag.ok{color:var(--accent-link);background:rgba(0,113,227,0.08)}
.tag.hold{color:var(--text-2);background:rgba(0,0,0,0.05)}
.tag.bad{color:var(--heat);background:rgba(255,107,0,0.1)}
.prob{grid-column:1/-1;margin-top:5px;font-size:12.5px;color:var(--heat);line-height:1.55}
.picks{margin-top:11px;display:flex;flex-wrap:wrap;gap:7px}
.pick{font-size:12.5px;font-weight:550;color:var(--accent-link);background:rgba(0,113,227,0.08);
  border-radius:var(--r-pill);padding:6px 13px;
  transition:background 180ms ease,transform 160ms var(--ease-out)}
.pick:active{transform:scale(0.97)}
.empty{padding:44px 24px;text-align:center;color:var(--text-4);font-size:13.5px;line-height:1.8}

@media(hover:hover) and (pointer:fine){
  .mic:hover{background:rgba(0,0,0,0.08)}
  .mic.rec:hover{background:var(--alarm)}
  .send:hover{box-shadow:0 12px 30px rgba(0,113,227,0.28)}
  .hint:hover{background:rgba(0,113,227,0.08);color:var(--accent-link)}
  .pick:hover{background:rgba(0,113,227,0.15)}
}
/* 减少动效 ≠ 没有动效：留下透明度和颜色，去掉位移和模糊 */
@media(prefers-reduced-motion:reduce){
  .entry{transform:none;filter:none;transition:opacity 200ms ease}
  .wchip{transform:none;transition:opacity 200ms ease}
  .mic.rec,.dot.up{animation:none}
  .mic:active,.send:active,.hint:active,.pick:active{transform:none}
  .feed::after{transition:outline-color 200ms ease}
  .tv.blink{filter:none}
}
</style></head><body>

<header><div class="nav">
  <div class="brand">两栖跟拍机 · 地面大脑<em>说一句话，看它怎么想</em></div>
  <div class="link"><span class="dot" id="d_link"></span><span id="t_link">连接中…</span></div>
</div></header>

<main>
  <div>
    <div class="panel">
      <div class="phead">
        <div class="ptitle">机上画面</div>
        <div class="pnote"><span class="dot" id="d_tgt"></span><span id="t_tgt">—</span></div>
      </div>
      <div class="feed" id="feed">
        <img id="cam" alt="">
        <div class="off" id="camoff" hidden><span>画面没接上</span><span id="camurl"></span></div>
      </div>
      <div class="tele">
        <div><div class="tk">形态</div><div class="tv" id="v_domain">—</div></div>
        <div><div class="tk">取景</div><div class="tv" id="v_mode">—</div></div>
        <div><div class="tk">高度</div><div class="tv" id="v_h">—</div></div>
        <div><div class="tk">跟随</div><div class="tv" id="v_follow">—</div></div>
        <div><div class="tk">tracker</div><div class="tv" id="v_tracker">—</div></div>
        <div><div class="tk">帧率</div><div class="tv" id="v_fps">—</div></div>
      </div>
      <div class="phead" style="border-top:1px solid var(--hairline)">
        <div class="ptitle">画面里有谁</div>
        <div class="pnote">grounding 就从这几个里挑</div>
      </div>
      <div class="who" id="who" style="border-top:none;padding-top:0"></div>
    </div>
  </div>

  <div>
    <div class="panel">
      <div class="phead"><div class="ptitle">对它说</div>
        <div class="pnote" id="t_mode"></div></div>
      <div class="say">
        <div class="sayrow">
          <button class="mic" id="mic" aria-label="说话">
            <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor"
                 stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z"/>
              <path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/>
            </svg>
          </button>
          <textarea id="say" rows="1" placeholder="跟穿红衣服的那个人"></textarea>
          <button class="send" id="send"><span>发送</span></button>
        </div>
        <div class="hints" id="hints"></div>
        <div class="note" id="micnote"></div>
      </div>
    </div>

    <div class="panel">
      <div class="phead"><div class="ptitle">它怎么想的</div></div>
      <div class="log" id="log">
        <div class="empty" id="empty">还没说话。<br>说一句，这里会显示它听懂成什么、<br>从画面里挑了谁、给板子发了什么。</div>
      </div>
    </div>
  </div>
</main>

<script>
var STREAM = "__STREAM__", BOARD = "__BOARD__", GMODE = "__GMODE__";
var $ = function(id){ return document.getElementById(id); };
$('t_mode').textContent = GMODE;
$('camurl').textContent = STREAM;

var cam = $('cam');
cam.onerror = function(){ cam.hidden = true; $('camoff').hidden = false; };
/* ?frame=1 → 用单帧接口。MJPEG 是永不结束的响应，挂着它 load 事件永远不触发，
   截图/录屏工具会一直等。要截图就带这个参数，画面照旧是真的。 */
if (location.search.indexOf('frame=1') >= 0) {
  cam.src = '/api/frame?t=' + Date.now();
} else {
  cam.src = STREAM;
}

/* ── 快捷句 ── */
["跟穿红衣服的那个人","起飞跟着我，定高两米四","镜头放平，正常跟拍",
 "贴地走着跟","跟着那个人","停一下","返航"].forEach(function(h){
  var b = document.createElement('button');
  b.className = 'hint'; b.type = 'button'; b.textContent = h;
  b.onclick = function(){ say.value = h; resize(); say.focus(); };
  $('hints').appendChild(b);
});

var say = $('say'), sendBtn = $('send');
function resize(){ say.style.height = 'auto'; say.style.height = Math.min(132, say.scrollHeight) + 'px'; }
say.addEventListener('input', resize);
say.addEventListener('keydown', function(e){
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});
sendBtn.onclick = send;

/* ── 只在值真变了才写 DOM：轮询 700ms，每次重绘会让数字一直在闪 ── */
var shown = {};
function put(id, text, cls){
  var el = $(id), key = text + '|' + (cls || '');
  if (shown[id] === key) return;
  var first = shown[id] === undefined;
  shown[id] = key;
  /* 只动 blink 之外的类 —— 整个 className 覆写会把 blink 抹掉，过渡就没了 */
  var apply = function(){
    el.innerHTML = text;
    ['on','warn','dim'].forEach(function(c){ el.classList.remove(c); });
    if (cls) el.classList.add(cls);
  };
  if (first) { apply(); return; }
  el.classList.add('blink');                                  // 模糊一下遮住跳变
  setTimeout(apply, 90);
  setTimeout(function(){ el.classList.remove('blink'); }, 180);
}
function num(v, unit, digits){
  return v === null || v === undefined ? '—'
       : v.toFixed(digits === undefined ? 1 : digits) + (unit ? '<u>' + unit + '</u>' : '');
}

function paintBoard(s, up){
  $('d_link').className = 'dot ' + (up ? 'up' : 'down');
  $('t_link').textContent = up ? '板子在线' : ('连不上 ' + BOARD);
  if (!s) return;
  put('v_domain', s.domain === 'air' ? '空中' : '地面', s.domain === 'air' ? 'on' : '');
  put('v_mode', s.mode === 'pitch' ? '定高俯拍' : (s.mode === 'level' ? '云台锁平' : '—'), '');
  put('v_h', num(s.height_m, 'm'), s.mode === 'pitch' ? '' : 'dim');
  put('v_follow', s.follow_enabled ? '跟随中' : '待命', s.follow_enabled ? 'on' : 'dim');
  put('v_tracker', s.tracker_state || '—', s.target_present ? '' : 'warn');
  put('v_fps', num(s.fps, null, 1), s.fps >= 8 ? '' : 'warn');

  $('d_tgt').className = 'dot ' + (s.target_present ? 'up' : 'down');
  $('t_tgt').textContent = s.target_present ? '目标在画面里' : '目标丢了';

  /* 跟随开启的那一下，取景器边缘亮一下 —— 状态指示，不是装饰 */
  var feed = $('feed');
  if (s.follow_enabled !== paintBoard.wasFollowing) {
    paintBoard.wasFollowing = s.follow_enabled;
    if (s.follow_enabled) {
      feed.classList.add('armed');
      setTimeout(function(){ feed.classList.remove('armed'); }, 620);
    }
  }
}

var whoKey = '';
function paintWho(dets, ownerId){
  var key = JSON.stringify([dets, ownerId]);
  if (key === whoKey) return;              // 没变就别重绘，否则 chip 一直在闪
  whoKey = key;
  var box = $('who');
  box.innerHTML = '';
  if (!dets || !dets.length) {
    box.innerHTML = '<span class="wchip none in">画面里暂时没有目标</span>';
    return;
  }
  dets.forEach(function(d, i){
    var c = document.createElement('span');
    c.className = 'wchip' + (d.target_id === ownerId ? ' owner' : '');
    c.textContent = '#' + d.target_id + ' ' + d.label + (d.target_id === ownerId ? ' · 主人' : '');
    box.appendChild(c);
    setTimeout(function(){ c.classList.add('in'); }, 40 * i);   // 轻微错开
  });
}

function poll(){
  fetch('/api/board').then(function(r){ return r.json(); }).then(function(j){
    paintBoard(j.state, j.up);
    paintWho(j.detections, j.owner_id);
  }).catch(function(){ paintBoard(null, false); });
}
poll(); setInterval(poll, 700);

/* ── 日志 ── */
function esc(s){
  return String(s).replace(/[&<>"]/g, function(c){
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]; });
}
function row(k, v){ return '<div class="rk">' + k + '</div><div class="rv">' + v + '</div>'; }

var ACT = {follow:'跟随', lock:'锁定', search:'搜索', stop:'停止', 'return':'返航',
           orbit:'环绕', shoot:'抢镜', idle:'待命'};
var FORM = {fly:'飞', walk:'贴地走', auto:'不指定'};
var CAM = {level:'云台锁平', pitch:'定高俯拍', auto:'不指定'};

function render(j){
  $('empty').hidden = true;
  var i = j.intent, e = document.createElement('div');
  e.className = 'entry';

  var tag = j.sent ? '<span class="tag ok">已下发</span>'
                   : '<span class="tag hold">' + (i.needs_confirm ? '等你点选' : '没下发') + '</span>';
  if (j.problems && j.problems.length) tag = '<span class="tag bad">部分落空</span>';

  var h = '<div class="heard"><span>「' + esc(j.text) + '」</span>' + tag + '</div><div class="rows">';
  var what = '<b>' + (ACT[i.action] || esc(i.action)) + '</b>';
  if (i.form !== 'auto') what += ' · ' + FORM[i.form];
  if (i.height_m !== null && i.height_m !== undefined) what += ' · ' + i.height_m.toFixed(1) + ' m';
  if (i.camera_mode !== 'auto') what += ' · ' + CAM[i.camera_mode];
  h += row('听懂', what);
  if (i.target_id !== null && i.target_id !== undefined)
    h += row('挑的人', '<b>#' + i.target_id + ' ' + esc(j.target_label || '') + '</b>');
  if (i.reason) h += row('它的理由', esc(i.reason));
  if (j.patch && Object.keys(j.patch).length)
    h += '<div class="wire">POST /api/state　' + esc(JSON.stringify(j.patch)) + '</div>';
  if (j.board)
    h += row('板子回', 'mode=' + j.board.mode + '　domain=' + j.board.domain +
             '　follow=' + j.board.follow_enabled + '　h=' + j.board.height_m + 'm');
  (j.problems || []).forEach(function(p){ h += '<div class="prob">⚠️ ' + esc(p) + '</div>'; });
  h += '</div>';
  e.innerHTML = h;

  if (i.needs_confirm && i.candidates && i.candidates.length) {
    var picks = document.createElement('div');
    picks.className = 'picks';
    i.candidates.forEach(function(tid){
      var b = document.createElement('button');
      b.className = 'pick'; b.type = 'button';
      b.textContent = '就跟 #' + tid + ' ' + ((j.candidate_labels || {})[tid] || '');
      b.onclick = function(){ pick(tid); };
      picks.appendChild(b);
    });
    e.appendChild(picks);
  }

  var log = $('log');
  log.insertBefore(e, log.firstChild);
  requestAnimationFrame(function(){ requestAnimationFrame(function(){ e.classList.add('in'); }); });
}

function post(url, body){
  sendBtn.classList.add('busy'); sendBtn.disabled = true;
  return fetch(url, {method:'POST', body: JSON.stringify(body)})
    .then(function(r){ return r.json(); })
    .then(function(j){ render(j); poll(); })
    .catch(function(err){
      render({text:'（发送失败）', intent:{action:'idle', form:'auto', camera_mode:'auto',
              reason:String(err), needs_confirm:false, candidates:[]},
              sent:false, patch:{}, board:null, problems:[String(err)]});
    })
    .finally(function(){ sendBtn.classList.remove('busy'); sendBtn.disabled = false; });
}
function send(){
  var t = say.value.trim(); if (!t) return;
  say.value = ''; resize();
  post('/api/say', {text: t});
}
function pick(tid){ post('/api/pick', {target_id: tid}); }

/* ── 语音：识别的字先上屏、可以改，不自动发 —— 现场听错了能秒改 ── */
var SR = window.SpeechRecognition || window.webkitSpeechRecognition;
var mic = $('mic'), micnote = $('micnote');
if (!SR) {
  mic.disabled = true;
  micnote.textContent = '这个浏览器没有语音识别（Chrome 才有）—— 打字一样用。';
} else {
  micnote.textContent = '点麦克风说话。识别出的字会先填进框里，改完再发。';
  var rec = new SR(), running = false;
  rec.lang = 'zh-CN'; rec.interimResults = true; rec.continuous = false;
  mic.onclick = function(){ running ? rec.stop() : rec.start(); };
  rec.onstart = function(){ running = true; mic.classList.add('rec'); };
  rec.onend = function(){ running = false; mic.classList.remove('rec'); say.focus(); };
  rec.onerror = function(e){ micnote.textContent = '语音识别出错（' + e.error + '）—— 打字发一样的。'; };
  rec.onresult = function(e){
    var t = '';
    for (var i = 0; i < e.results.length; i++) t += e.results[i][0].transcript;
    say.value = t; resize();
  };
}
</script></body></html>
"""


def grab_frame(stream_url: str, timeout: float = 5.0, limit: int = 3_000_000):
    """从 MJPEG 流里抓一帧完整 JPEG。

    为什么要这个：MJPEG 是一个永不结束的响应，`<img>` 挂着它页面的 load 事件
    就永远不触发 —— 任何截图工具（headless Chrome / 录屏脚本）都会卡死等 load。
    带 `?frame=1` 打开页面就用这个单帧接口，画面还是真的，但页面能加载完。
    """
    try:
        with urllib.request.urlopen(stream_url, timeout=timeout) as r:
            buf = b""
            while len(buf) < limit:
                chunk = r.read(16384)
                if not chunk:
                    break
                buf += chunk
                start = buf.find(b"\xff\xd8")           # JPEG SOI
                if start >= 0:
                    end = buf.find(b"\xff\xd9", start + 2)  # EOI
                    if end >= 0:
                        return buf[start:end + 2]
    except Exception:
        pass
    return None


class Console:
    """把 agent 和板子包在一起，给页面提供几个接口。"""

    def __init__(self, board_url: str, stream_url: str, feed: str):
        self.board_url = board_url.rstrip("/")
        self.stream_url = stream_url
        self.downlink = StateApiDownlink(base_url=self.board_url, verbose=False)
        mem = PreferenceMemory()
        mem.remember_owner("穿红色卫衣的男生", "红")
        self.agent = GroundAgent(MockYoloSource(feed), MockTelemetry(),
                                 downlink=self.downlink, memory=mem)
        self._last = Intent(action="follow")
        self._lock = threading.Lock()

    # ---------- 给页面的数据 ----------
    def board(self) -> dict:
        try:
            state = self.downlink.read_state()
            up = True
        except Exception:
            state, up = None, False
        frame = self.agent.yolo.latest()
        owner = self.agent.memory.match_owner(frame)
        return {
            "up": up, "state": state, "owner_id": owner,
            "detections": [{"target_id": d.target_id, "cls": d.cls,
                            "label": d.label, "conf": d.conf}
                           for d in frame.detections],
        }

    def _pack(self, text: str, d) -> dict:
        i = d.intent
        det = d.frame.by_id(i.target_id) if i.target_id is not None else None
        res = d.result or {}
        return {
            "text": text,
            "intent": i.to_dict(),
            "target_label": det.label if det else "",
            "candidate_labels": {
                str(t): (d.frame.by_id(t).label if d.frame.by_id(t) else "")
                for t in i.candidates},
            "sent": d.sent,
            "patch": res.get("patch") or {},
            "board": res.get("state"),
            "problems": res.get("problems") or [],
        }

    def say(self, text: str) -> dict:
        with self._lock:
            d = self.agent.handle(text)
            self._last = d.intent
            return self._pack(text, d)

    def pick(self, target_id: int) -> dict:
        with self._lock:
            d = self.agent.confirm_pick(target_id, self._last)
            self._last = d.intent
            return self._pack(f"（点选了 #{target_id}）", d)


class _Handler(BaseHTTPRequestHandler):
    console: Console = None
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body: bytes, ctype: str):
        if code >= 400:
            self.close_connection = True
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(200, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    def do_GET(self):
        c = self.console
        if self.path == "/":
            import llm
            page = (PAGE.replace("__STREAM__", c.stream_url)
                        .replace("__BOARD__", c.board_url)
                        .replace("__GMODE__",
                                 "grounding：LLM" if llm.available() else "grounding：规则降级（没 key）"))
            self._send(200, page.encode(), "text/html; charset=utf-8")
        elif self.path == "/api/board":
            self._json(c.board())
        elif self.path.startswith("/api/frame"):
            jpg = grab_frame(c.stream_url)
            if jpg:
                self._send(200, jpg, "image/jpeg")
            else:
                self._send(503, b"no frame", "text/plain")
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self):
        c = self.console
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._send(400, b"bad length", "text/plain"); return
        if n < 0 or n > MAX_BODY:
            self._send(413, b"too large", "text/plain"); return
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            self._send(400, b"bad json", "text/plain"); return

        try:
            if self.path == "/api/say":
                self._json(c.say(str(body.get("text", ""))[:400]))
            elif self.path == "/api/pick":
                self._json(c.pick(int(body.get("target_id"))))
            else:
                self._send(404, b"not found", "text/plain")
        except Exception as e:
            self._json({"text": "", "intent": Intent(action="idle",
                        reason=f"出错：{type(e).__name__}: {e}").to_dict(),
                        "sent": False, "patch": {}, "board": None,
                        "problems": [f"{type(e).__name__}: {e}"],
                        "target_label": "", "candidate_labels": {}})


def arg(name: str, default: str) -> str:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


def main():
    board = arg("--board", "http://localhost:8080")
    stream = arg("--stream", "http://localhost:8000/stream")
    feed = arg("--feed", "mock_data/feed_follow_then_lost.jsonl")
    port = int(arg("--port", str(CONSOLE_PORT)))

    console = Console(board, stream, feed)
    handler = type("H", (_Handler,), {"console": console})
    srv = ThreadingHTTPServer(("0.0.0.0", port), handler)
    print(f"操作台   http://localhost:{port}")
    print(f"板子     {board}")
    print(f"画面     {stream}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
        console.downlink.stop_heartbeat()


if __name__ == "__main__":
    main()
