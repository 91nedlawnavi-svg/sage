(function(){
  var CFG={CHAT:'/api/chat',HISTORY:'/api/history',REFLECTIONS:'/reflections?n=12',FINDINGS:'/findings?n=12',STATUS:'/health',DESK:'/api/desk/promotions',IMPRESSIONS:'/api/desk/impressions',REFRESH_MS:15000};
  var $=function(id){return document.getElementById(id);};
  var messagesEl=$('messages'),inputEl=$('user-input'),sendBtn=$('send-btn');
  var sending=false,activeTab='reflections';var sageGraphCtl=null,sageGraphRefreshWired=false;
  var FENCE=String.fromCharCode(96,96,96);
  var codeRe=new RegExp(FENCE+'[\\w]*\\n?([\\s\\S]*?)'+FENCE,'g');

  function escapeHtml(t){return String(t).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
  function escapeAttr(t){return escapeHtml(t).replace(/"/g,'&quot;').replace(/'/g,'&#39;');}
  function renderMarkdown(text){
    var s=escapeHtml(text);
    s=s.replace(codeRe,function(_,c){return '<pre><code>'+c.replace(/\\s+$/,'')+'</code></pre>';});
    s=s.replace(/`([^`]+)`/g,'<code>$1</code>');
    s=s.replace(/\\*\\*([^*]+)\\*\\*/g,'<strong>$1</strong>');
    s=s.replace(/\\*([^*]+)\\*/g,'<em>$1</em>');
    s=s.replace(/\\n/g,'<br>');
    return s;
  }
  function scrollBottom(){messagesEl.scrollTop=messagesEl.scrollHeight;}
  function hideEmpty(){var e=$('empty');if(e)e.remove();}
  function fmtClock(v){var d=(v==null)?new Date():((typeof v==='number')?new Date(v<1e12?v*1000:v):new Date(v));if(isNaN(d))d=new Date();return ('0'+d.getHours()).slice(-2)+'.'+('0'+d.getMinutes()).slice(-2);}
  function stampTime(m,v){if(!m||m.querySelector('.time'))return;var t=document.createElement('div');t.className='time';t.textContent=fmtClock(v);m.appendChild(t);}
  function addHcDot(bubble,msgId,held){if(!msgId)return;var d=document.createElement('span');d.className='hc-dot'+(held?' held':'');d.title=held?'held close — tap to release':'tap to hold close';d.addEventListener('click',function(e){e.stopPropagation();var nowHeld=!d.classList.contains('held');fetch('/api/episodes/'+encodeURIComponent(msgId)+'/held-close',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({held:nowHeld})}).then(function(r){return r.json();}).then(function(j){if(j&&j.ok){d.classList.toggle('held',nowHeld);d.title=nowHeld?'held close — tap to release':'tap to hold close';}}).catch(function(){});});bubble.appendChild(d);}
  function addUser(text,ts,msgId,held){hideEmpty();var m=document.createElement('div');m.className='msg user';var b=document.createElement('div');b.className='bubble';b.textContent=text;addHcDot(b,msgId,held);m.appendChild(b);stampTime(m,ts);messagesEl.appendChild(m);scrollBottom();return m;}
  function addAi(ts,msgId,held){hideEmpty();var m=document.createElement('div');m.className='msg ai';var b=document.createElement('div');b.className='bubble';addHcDot(b,msgId,held);m.appendChild(b);if(ts!==undefined&&ts!==null)stampTime(m,ts);messagesEl.appendChild(m);scrollBottom();return b;}
  function addThinking(){hideEmpty();var m=document.createElement('div');m.className='msg ai';m.id='thinking';var t=document.createElement('div');t.className='thinking';t.innerHTML='<span></span><span></span><span></span>';m.appendChild(t);messagesEl.appendChild(m);scrollBottom();}
  function rmThinking(){var e=$('thinking');if(e)e.remove();}
  function addSearching(){if($('searching'))return;hideEmpty();var m=document.createElement('div');m.className='msg ai';m.id='searching';var t=document.createElement('div');t.className='searching';t.innerHTML='<span class="dot"></span><span class="label">Searching the web…</span>';m.appendChild(t);messagesEl.appendChild(m);scrollBottom();}
  function rmSearching(){var e=$('searching');if(e)e.remove();}
  function setSending(v){sending=v;sendBtn.disabled=v;}

  function msgNumId(id){var m=String(id||'').match(/_(\d+)$/);return m?parseInt(m[1],10):0;}
  function scrollNow(){messagesEl.scrollTo({top:messagesEl.scrollHeight,behavior:'auto'});}
  function nearBottom(){return messagesEl.scrollHeight-messagesEl.scrollTop-messagesEl.clientHeight<90;}
  function send(){
    var text=inputEl.value.trim();
    if(!text||sending)return;
    addUser(text);inputEl.value='';autoResize();setSending(true);var isSearch=/^\/search(\s|$)/i.test(text);if(isSearch){addSearching();}else{addThinking();}
    var bubble=null,acc='',frameErr=false;var RS=String.fromCharCode(30),sbuf='',inFrame=false;
    function ensureBubble(){if(!bubble){rmThinking();rmSearching();bubble=addAi();bubble.classList.add('streaming');}}
    fetch(CFG.CHAT,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})})
      .then(function(r){
        if(!r.ok)throw new Error('HTTP '+r.status);
        if(!r.body||!r.body.getReader)return r.text().then(function(t){acc=t.split(RS).filter(function(_,i){return i%2===0;}).join('');ensureBubble();bubble.innerHTML=renderMarkdown(acc);});
        var reader=r.body.getReader(),decoder=new TextDecoder();
        function onFrame(js){try{var ev=JSON.parse(js);if(ev&&ev.event==='search_started'){addSearching();}else if(ev&&ev.event==='search_done'){rmSearching();if(!bubble)addThinking();}else if(ev&&ev.event==='error'){frameErr=true;rmThinking();rmSearching();var eb=addAi();eb.className='bubble error';eb.textContent=ev.message||'Something went wrong.';stampTime(eb.parentElement);}}catch(e){}}
        function feed(chunk){sbuf+=chunk;var out='';while(true){var i=sbuf.indexOf(RS);if(i===-1){if(!inFrame){out+=sbuf;sbuf='';}break;}if(!inFrame){out+=sbuf.slice(0,i);sbuf=sbuf.slice(i+1);inFrame=true;}else{onFrame(sbuf.slice(0,i));sbuf=sbuf.slice(i+1);inFrame=false;}}return out;}
        return (function pump(){return reader.read().then(function(res){
          if(res.done)return;
          var text=feed(decoder.decode(res.value,{stream:true}));
          if(text){var stick=nearBottom();ensureBubble();acc+=text;bubble.innerHTML=renderMarkdown(acc);if(stick)scrollNow();}
          return pump();
        });})();
      })
      .then(function(){rmThinking();rmSearching();if(frameErr&&!acc.trim()){if(bubble)bubble.classList.remove('streaming');return;}if(!bubble){bubble=addAi();}bubble.classList.remove('streaming');if(acc&&acc.trim())bubble.innerHTML=renderMarkdown(acc);else bubble.textContent='(she went quiet)';stampTime(bubble.parentElement);})
      .catch(function(e){rmThinking();rmSearching();var b=bubble||addAi();b.className='bubble error';b.textContent='Could not reach Sage: '+e.message;stampTime(b.parentElement);})
      .then(function(){setSending(false);inputEl.focus();if(nearBottom())scrollBottom();refreshInnerHint();});
  }

  function loadHistory(){
    var clearedMarker=parseInt(localStorage.getItem('sage:chatClearedBefore')||'0',10);
    fetch(CFG.HISTORY).then(function(r){return r.ok?r.json():null;}).then(function(data){
      if(!data)return;var msgs=data.messages||[];if(!msgs.length)return;
      var shown=0;
      msgs.forEach(function(m){
        if(m.kind!=='waiting'&&clearedMarker>0&&m.id&&msgNumId(m.id)<=clearedMarker)return;
        if(!shown)hideEmpty();shown++;
        if(m.role==='user'){addUser(m.content,m.ts,m.id,!!m.held_close);if(m.id)messagesEl.lastElementChild.setAttribute('data-msg-id',m.id);}
        else{var b=addAi(m.ts,m.id,!!m.held_close);b.innerHTML=renderMarkdown(m.content||'');if(m.id)messagesEl.lastElementChild.setAttribute('data-msg-id',m.id);}
      });
      if(shown>0)scrollBottom();
    }).catch(function(){});
  }

  function openDrawer(){$('drawer').classList.add('open');$('drawer-overlay').classList.add('show');markSeen();if(activeTab==='graph')showGraphTab();else loadTab(activeTab);}
  function closeDrawer(){$('drawer').classList.remove('open');$('drawer-overlay').classList.remove('show');if(sageGraphCtl)sageGraphCtl.stop();}
  function switchTab(tab){if(activeTab===tab)return;if(activeTab==='graph'&&sageGraphCtl)sageGraphCtl.stop();activeTab=tab;var t=document.querySelectorAll('.drawer-tab');for(var i=0;i<t.length;i++){t[i].classList.toggle('active',t[i].getAttribute('data-tab')===tab);}if(tab==='graph')showGraphTab();else{hideGraphTab();loadTab(tab);}}

  function pick(o,keys){for(var i=0;i<keys.length;i++){var k=keys[i];if(o&&o[k]!=null&&o[k]!=='')return o[k];}return null;}
  function fmtTime(v){if(v==null)return '';var d=(typeof v==='number')?new Date(v<1e12?v*1000:v):new Date(v);if(isNaN(d))return String(v);return d.toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});}
  function normalizeList(data,keys){if(Array.isArray(data))return data;for(var i=0;i<keys.length;i++){if(data&&Array.isArray(data[keys[i]]))return data[keys[i]];}return [];}

  function loadTab(tab){
    var c=$('drawer-content'),note=$('drawer-note');
    if(tab==='desk'){loadDesk(c,note);return;}
    if(tab==='impressions'){loadImpressions(c,note);return;}
    var isRef=(tab==='reflections');
    note.textContent=isRef?'What she has turned over on her own. Read-only.':'What she went looking for, and found. Read-only.';
    fetch(isRef?CFG.REFLECTIONS:CFG.FINDINGS).then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}).then(function(data){
      if(tab!==activeTab)return;
      var list=normalizeList(data,[tab,'memories','items','results','data']).slice().reverse();
      if(!list.length){c.innerHTML='<div class=\"drawer-empty\">Nothing yet. Leave her idle a while and she will wander.</div>';return;}
      c.innerHTML='';
      list.forEach(function(it){
        var ts=pick(it,['timestamp','created_at','created','time','ts','date']);
        var body=pick(it,['text','content','body','reflection','finding','query','summary','thought'])||(typeof it==='string'?it:JSON.stringify(it));
        var results=(it.results&&it.results.length)?it.results:null;
        var src=results?null:pick(it,['source','url','link','href']);
        var el=document.createElement('div');el.className='entry '+(isRef?'reflection':'finding');
        if(ts){var tsEl=document.createElement('div');tsEl.className='ts';tsEl.textContent=fmtTime(ts);el.appendChild(tsEl);}
        var bodyEl=document.createElement('div');bodyEl.className='body';bodyEl.textContent=body;el.appendChild(bodyEl);
        if(results||src){var srcEl=document.createElement('div');srcEl.className='src';
          if(results){srcEl.innerHTML=results.map(function(r){var u=r.url||r.link||r.href||'';var label=r.title||r.name||u;return u?'<a href=\"'+escapeAttr(u)+'\" target=\"_blank\" rel=\"noopener\">'+escapeHtml(label)+'</a>':escapeHtml(label);}).join(' &middot; ');}
          else{srcEl.innerHTML='<a href=\"'+escapeAttr(src)+'\" target=\"_blank\" rel=\"noopener\">'+escapeHtml(src)+'</a>';}
          el.appendChild(srcEl);}
        c.appendChild(el);
      });
    }).catch(function(e){if(tab!==activeTab)return;c.innerHTML='<div class=\"drawer-empty\">Cannot read her '+(isRef?'reflections':'findings')+' yet ('+e.message+').</div>';});
  }

  function loadDesk(c,note){
    note.textContent='Proposed facts waiting on you.';
    c.innerHTML='<div class="drawer-empty">Loading…</div>';
    fetch(CFG.DESK).then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}).then(function(data){
      if(activeTab!=='desk')return;
      var list=(data&&data.promotions)||[];
      if(!list.length){c.innerHTML='<div class="drawer-empty">Nothing waiting on you.</div>';return;}
      c.innerHTML='';
      list.forEach(function(p){
        var f=p.proposed_fact||{};
        var factLabel=escapeHtml((f.predicate||'')+' '+((f.object_value!=null)?f.object_value:''));
        var imp=p.impression;
        var impHtml=imp?'<div class="desk-imp">'+escapeHtml(imp.statement||'')+' <span class="desk-support">'+escapeHtml(String(imp.support_count||0))+' supporting</span></div>':'';
        var eps=p.supporting_episodes||[];
        var epsHtml=eps.length?eps.map(function(e){return '<div class="desk-ep"><span class="ts">'+escapeHtml(fmtTime(e.ts))+'</span>'+escapeHtml(e.content||'')+'</div>';}).join(''):'';
        var epToggleHtml=eps.length?'<button class="desk-ep-toggle" type="button">'+eps.length+' receipt'+(eps.length===1?'':'s')+' ▾</button>':'';
        var card=document.createElement('div');card.className='desk-card';
        card.innerHTML=
          '<div class="desk-fact">'+factLabel+'</div>'+
          impHtml+
          epToggleHtml+
          (epsHtml?'<div class="desk-episodes">'+epsHtml+'</div>':'')+
          '<div class="desk-actions">'+
            '<button class="desk-approve" data-qid="'+escapeAttr(String(p.queue_id||''))+'">Approve</button>'+
            '<button class="desk-reject desk-actions-reject" data-qid="'+escapeAttr(String(p.queue_id||''))+'">Reject</button>'+
          '</div>';
        var tog=card.querySelector('.desk-ep-toggle');
        if(tog){tog.addEventListener('click',function(){var ep=card.querySelector('.desk-episodes');if(ep){ep.classList.toggle('open');tog.textContent=ep.classList.contains('open')?(eps.length+' receipt'+(eps.length===1?'':'s')+' ▴'):(eps.length+' receipt'+(eps.length===1?'':'s')+' ▾');}});}
        function wireDecide(btn,approve){btn.addEventListener('click',function(){btn.disabled=true;var sib=card.querySelector(approve?'.desk-actions-reject':'.desk-approve');if(sib)sib.disabled=true;fetch('/api/desk/decide',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({queue_id:p.queue_id,approve:approve})}).then(function(r){return r.json();}).then(function(){loadDesk(c,note);}).catch(function(){btn.disabled=false;if(sib)sib.disabled=false;});});}
        wireDecide(card.querySelector('.desk-approve'),true);
        wireDecide(card.querySelector('.desk-actions-reject'),false);
        c.appendChild(card);
      });
    }).catch(function(e){if(activeTab!=='desk')return;c.innerHTML='<div class="drawer-empty">Nothing waiting on you.</div>';});
  }

  function loadImpressions(c,note){
    note.textContent='Patterns she has noticed, weighted by evidence. Read-only.';
    c.innerHTML='<div class="drawer-empty">Loading…</div>';
    fetch(CFG.IMPRESSIONS).then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}).then(function(data){
      if(activeTab!=='impressions')return;
      var list=(data&&data.impressions)||[];
      if(!list.length){c.innerHTML='<div class="drawer-empty">No impressions formed yet.</div>';return;}
      c.innerHTML='';
      list.forEach(function(imp){
        var active=(imp.status==='active'||imp.status==null);
        var el=document.createElement('div');el.className='imp-item'+(active?'':' muted');
        el.innerHTML=
          '<div class="imp-stmt">'+escapeHtml(imp.statement||'')+'</div>'+
          '<div class="imp-meta">'+escapeHtml(String(imp.support_count||0))+' supporting'+
          (imp.status&&imp.status!=='active'?' · '+escapeHtml(imp.status):'')+
          (imp.ts_formed?' · '+escapeHtml(fmtTime(imp.ts_formed)):'')+
          '</div>';
        c.appendChild(el);
      });
    }).catch(function(e){if(activeTab!=='impressions')return;c.innerHTML='<div class="drawer-empty">No impressions formed yet.</div>';});
  }

  function latestTs(list){var max=0;list.forEach(function(it){var ts=pick(it,['timestamp','created_at','created','time','ts','date']);var d=(typeof ts==='number')?(ts<1e12?ts*1000:ts):Date.parse(ts);if(d&&d>max)max=d;});return max;}
  function refreshInnerHint(){fetch(CFG.REFLECTIONS).then(function(r){return r.ok?r.json():null;}).then(function(data){if(!data)return;var list=normalizeList(data,['reflections','memories','items','data']);if(!list.length)return;var latest=latestTs(list);var seen=parseInt(localStorage.getItem('sage_seen_ts')||'0',10);$('inner-toggle').classList.toggle('has-new',latest>seen);}).catch(function(){});}
  function markSeen(){fetch(CFG.REFLECTIONS).then(function(r){return r.ok?r.json():null;}).then(function(data){if(!data)return;var list=normalizeList(data,['reflections','memories','items','data']);localStorage.setItem('sage_seen_ts',String(latestTs(list)||Date.now()));$('inner-toggle').classList.remove('has-new');}).catch(function(){});}

  function setStatus(state){var d=$('status-dot');d.classList.remove('cold','down');if(state==='down'){d.classList.add('down');d.title='Problem reaching Sage';}else if(state==='cold'){d.classList.add('cold');d.title='Connecting';}else{d.title='Online';}}
  function checkStatus(){fetch(CFG.STATUS).then(function(r){if(r.status===404)throw{fb:true};if(!r.ok)throw{fb:false};return r.json().then(function(d){return d;},function(){return {};});}).then(function(d){if(d&&(d.ok===false||d.healthy===false||d.status==='error'||d.status==='down'))setStatus('down');else setStatus('ok');}).catch(function(e){if(e&&e.fb){fetch(CFG.HISTORY).then(function(r){setStatus(r.ok?'ok':'down');}).catch(function(){setStatus('down');});}else setStatus('down');});}

  function autoResize(){inputEl.style.height='auto';inputEl.style.height=Math.min(inputEl.scrollHeight,170)+'px';}

  sendBtn.addEventListener('click',send);
  inputEl.addEventListener('input',autoResize);
  inputEl.addEventListener('keydown',function(e){if(e.key==='Enter'&&(e.metaKey||e.ctrlKey)){e.preventDefault();send();}});
  function clearChat(){
    var newestKey=0,msgs=messagesEl.querySelectorAll('.msg[data-msg-id]');
    for(var i=0;i<msgs.length;i++){var k=msgNumId(msgs[i].getAttribute('data-msg-id'));if(k>newestKey)newestKey=k;}
    var prev=parseInt(localStorage.getItem('sage:chatClearedBefore')||'0',10);
    localStorage.setItem('sage:chatClearedBefore',String(Math.max(prev,newestKey,Date.now())));
    messagesEl.innerHTML='<div id="empty"><div id="empty-glyph">&#9671;</div><div id="empty-sub">Begin when you\'re ready.</div></div>';
  }
  $('clear-chat-btn').addEventListener('click',clearChat);
  $('inner-toggle').addEventListener('click',openDrawer);
  $('drawer-close').addEventListener('click',closeDrawer);
  $('drawer-overlay').addEventListener('click',closeDrawer);
  var tb=document.querySelectorAll('.drawer-tab');
  for(var i=0;i<tb.length;i++){(function(btn){btn.addEventListener('click',function(){switchTab(btn.getAttribute('data-tab'));});})(tb[i]);}

  loadHistory();checkStatus();refreshInnerHint();
  setInterval(function(){checkStatus();refreshInnerHint();if($('drawer').classList.contains('open')&&activeTab!=='graph')loadTab(activeTab);},CFG.REFRESH_MS);
  inputEl.focus();

  // Show "Call" link in header when voice is available
  fetch('/api/voice/status').then(function(r){return r.ok?r.json():null;}).then(function(d){
    if(d&&d.enabled){var cl=$('call-link');if(cl)cl.style.display='flex';}
  }).catch(function(){});

  // ── Graph tab ──
  var REVIEW_CAT_COLORS={family:"#c98a6f",friend:"#8ca37e",romantic:"#c08aa0",colleague:"#7f93a8",acquaintance:"#9a8d86",creator:"#c2a15e",other:"#857c73"};
  var reviewState={state:'pending',category:'',min:'',max:'',endpoint:'',items:[]};
  var reviewOpen=false;
  function showGraphTab(){
    var gc=$('sg-inner-graph'),dc=$('drawer-content'),note=$('drawer-note'),ge=$('sg-graph-error');
    if(dc)dc.style.display='none';
    if(note)note.textContent='Entity graph of people, places, and projects. Click a node or edge to curate.';
    if(ge)ge.style.display='none';
    if(gc)gc.style.display='flex';
    if(!sageGraphCtl){
      if(!window.initSageGraph){if(ge){ge.style.display='flex';ge.textContent='Graph module failed to load.';}return;}
      sageGraphCtl=window.initSageGraph({
        svg:$('sg-graph-svg'),chips:$('sg-graph-chips'),tooltip:$('sg-graph-tooltip'),
        getData:function(){return fetch('/api/graph').then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();});},
        egoId:'person:elliot',
        onSelect:onGraphSelect
      });
      if(!sageGraphCtl){if(ge){ge.style.display='flex';ge.textContent='Graph needs D3 (offline?).';}return;}
      if(!sageGraphRefreshWired){
        var rb=$('sg-graph-refresh');if(rb){rb.addEventListener('click',function(){if(sageGraphCtl)sageGraphCtl.reload();refreshReview();});}
        wireReviewUI();
        sageGraphRefreshWired=true;
      }
      sageGraphCtl.reload().then(null,function(e){if(ge){ge.style.display='flex';ge.textContent='Cannot load graph: '+e.message;}});
      refreshReview();
    }else{
      sageGraphCtl.resume();sageGraphCtl.refit();
      refreshReview();
    }
  }
  function hideGraphTab(){
    var gc=$('sg-inner-graph'),dc=$('drawer-content');
    if(gc)gc.style.display='none';
    if(dc)dc.style.display='';
    if(sageGraphCtl)sageGraphCtl.stop();
    closeInspector();
    closeReview();
  }

  // ── Click-to-curate inspector ──
  var inspectorTarget=null; // {kind:'edge'|'node', data:..., links:[link...]}
  function onGraphSelect(kind,d){
    if(kind==='edge'){inspectorTarget={kind:'edge',data:d};}
    else{inspectorTarget={kind:'node',data:d};}
    renderInspector();
  }
  function renderInspector(){
    var el=$('sg-inspector');if(!el){return;}
    if(!inspectorTarget){closeInspector();return;}
    el.classList.add('open');
    if(inspectorTarget.kind==='edge'){renderInspectorEdge(el,inspectorTarget.data);}
    else{renderInspectorNode(el,inspectorTarget.data);}
  }
  function nameOf(n){return (n&&(n.name||n.id))||'';}
  function renderInspectorEdge(el,l){
    var srcName=nameOf(l.source);var tgtName=nameOf(l.target);
    var srcId=(l.source&&(l.source.id||l.source))||'';
    var tgtId=(l.target&&(l.target.id||l.target))||'';
    var rid=l.id||l.relation_id||'';
    var locked=l.locked?' <span style="color:var(--accent);">locked</span>':'';
    el.innerHTML=
      '<div class="sg-insp-head">Edge <span class="sg-insp-kind">'+escapeHtml(l.predicate||'')+'</span>'+
      '<button class="sg-insp-close" type="button" aria-label="Close">&#10005;</button></div>'+
      '<div class="sg-insp-body">'+
      '<div class="sg-insp-row"><span>from</span>'+escapeHtml(srcName)+'</div>'+
      '<div class="sg-insp-row"><span>to</span>'+escapeHtml(tgtName)+'</div>'+
      '<div class="sg-insp-row"><span>predicate</span>'+escapeHtml(l.predicate||'')+'</div>'+
      '<div class="sg-insp-row"><span>category</span>'+escapeHtml(l.category||'other')+'</div>'+
      '<div class="sg-insp-row"><span>confidence</span>'+(l.confidence!=null?Number(l.confidence).toFixed(2):'-')+locked+'</div>'+
      '<div class="sg-insp-row"><span>id</span><code style="font-size:10.5px;">'+escapeHtml(rid||'(none)')+'</code></div>'+
      '</div>'+
      '<div class="sg-insp-actions">'+
        '<button data-act="confirm"'+(rid?'':' disabled')+'>Confirm</button>'+
        '<button data-act="fix"'+(rid?'':' disabled')+'>Fix…</button>'+
        '<button class="sg-danger" data-act="delete"'+(rid?'':' disabled')+'>Delete</button>'+
      '</div>'+
      '<div class="sg-insp-fix" data-fix>'+
        '<input data-fix-pred placeholder="predicate" value="'+escapeAttr(l.predicate||'')+'">'+
        '<select data-fix-kind>'+
          '<option value="entity">entity</option>'+
          '<option value="literal">literal</option>'+
        '</select>'+
        '<input data-fix-val placeholder="object id or value" value="'+escapeAttr(tgtId)+'">'+
        '<button data-act="fix-go">Save</button>'+
        '<button data-act="fix-cancel">Cancel</button>'+
      '</div>'+
      '<div class="sg-insp-status" data-status></div>';
    bindInspector(el,{kind:'edge',id:rid,subject_id:srcId,predicate:l.predicate,object_value:tgtId,object_kind:'entity'});
  }
  function renderInspectorNode(el,d){
    var facts=d.facts||[];
    var factRows=facts.map(function(f){
      var rid=f.relation_id||'';
      return '<div class="sg-insp-row" data-fact-row data-rid="'+escapeAttr(rid)+'" data-pred="'+escapeAttr(f.predicate||'')+'" data-val="'+escapeAttr(String(f.value==null?'':f.value))+'">'+
        '<span>'+escapeHtml(f.predicate||'')+'</span>'+escapeHtml(String(f.value==null?'':f.value))+
        (f.locked?' <span style="color:var(--accent);font-size:10px;">locked</span>':'')+
        ' &middot; <a href="#" data-act="fact-confirm" data-rid="'+escapeAttr(rid)+'" style="color:var(--muted);">confirm</a>'+
        ' &middot; <a href="#" data-act="fact-delete" data-rid="'+escapeAttr(rid)+'" style="color:var(--muted);">delete</a>'+
        '</div>';
    }).join('');
    el.innerHTML=
      '<div class="sg-insp-head">'+escapeHtml(d.name||d.id||'')+' <span class="sg-insp-kind">'+escapeHtml(d.type||'')+'</span>'+
      '<button class="sg-insp-close" type="button" aria-label="Close">&#10005;</button></div>'+
      '<div class="sg-insp-body">'+
      '<div class="sg-insp-row"><span>id</span><code style="font-size:10.5px;">'+escapeHtml(d.id||'')+'</code></div>'+
      (factRows||'<div class="sg-insp-row" style="color:var(--faint);">no literal facts</div>')+
      '</div>'+
      '<div class="sg-insp-status" data-status>Node deletion is out of scope. Use edge actions or the fact links above.</div>';
    bindInspectorNode(el);
  }
  function bindInspector(el,ctx){
    var close=el.querySelector('.sg-insp-close');if(close)close.addEventListener('click',closeInspector);
    var status=el.querySelector('[data-status]');
    function setStatus(txt,err){if(!status)return;status.textContent=txt||'';status.classList.toggle('err',!!err);}
    function disableAll(v){var bs=el.querySelectorAll('button');for(var i=0;i<bs.length;i++)bs[i].disabled=v;}
    el.querySelectorAll('.sg-insp-actions button').forEach(function(b){
      b.addEventListener('click',function(){
        var act=b.getAttribute('data-act');
        if(act==='confirm'){
          disableAll(true);setStatus('Confirming…');
          postWrite('/api/graph/confirm',{relation_id:ctx.id,subject_id:ctx.subject_id,predicate:ctx.predicate,object_value:ctx.object_value,object_kind:ctx.object_kind})
            .then(function(j){if(j&&j.ok){setStatus('Confirmed.');refreshAll();closeInspector();}else{setStatus((j&&j.error)||'Failed.',true);disableAll(false);}})
            .catch(function(e){setStatus(e.message,true);disableAll(false);});
        }else if(act==='delete'){
          disableAll(true);setStatus('Deleting…');
          postWrite('/api/graph/delete',{relation_id:ctx.id})
            .then(function(j){if(j&&j.ok){setStatus('Deleted.');refreshAll();closeInspector();}else{setStatus((j&&j.error)||'Failed.',true);disableAll(false);}})
            .catch(function(e){setStatus(e.message,true);disableAll(false);});
        }else if(act==='fix'){
          var fix=el.querySelector('[data-fix]');if(fix)fix.classList.add('show');
        }
      });
    });
    var fixGo=el.querySelector('[data-act="fix-go"]');
    if(fixGo){fixGo.addEventListener('click',function(){
      var pred=el.querySelector('[data-fix-pred]').value.trim();
      var kind=el.querySelector('[data-fix-kind]').value;
      var val=el.querySelector('[data-fix-val]').value.trim();
      if(!val){setStatus('object_value required',true);return;}
      disableAll(true);setStatus('Saving fix…');
      postWrite('/api/graph/fix',{relation_id:ctx.id,new_predicate:pred||null,new_object_value:val,new_object_kind:kind})
        .then(function(j){if(j&&j.ok){setStatus('Fixed.');refreshAll();closeInspector();}else{setStatus((j&&j.error)||'Failed.',true);disableAll(false);}})
        .catch(function(e){setStatus(e.message,true);disableAll(false);});
    });}
    var fixCancel=el.querySelector('[data-act="fix-cancel"]');
    if(fixCancel){fixCancel.addEventListener('click',function(){var fix=el.querySelector('[data-fix]');if(fix)fix.classList.remove('show');});}
  }
  function bindInspectorNode(el){
    var close=el.querySelector('.sg-insp-close');if(close)close.addEventListener('click',closeInspector);
    el.querySelectorAll('[data-act="fact-confirm"],[data-act="fact-delete"]').forEach(function(a){
      a.addEventListener('click',function(ev){
        ev.preventDefault();
        var act=a.getAttribute('data-act');
        var rid=a.getAttribute('data-rid');
        if(!rid)return;
        var url=act==='fact-confirm'?'/api/graph/confirm':'/api/graph/delete';
        postWrite(url,{relation_id:rid}).then(function(j){
          if(j&&j.ok){refreshAll();closeInspector();}
        }).catch(function(){});
      });
    });
  }
  function closeInspector(){var el=$('sg-inspector');if(el){el.classList.remove('open');el.innerHTML='';}inspectorTarget=null;}

  // ── Review queue ──
  function wireReviewUI(){
    var toggle=$('sg-review-toggle');if(toggle){toggle.addEventListener('click',function(){if(reviewOpen)closeReview();else openReview();});}
    var close=$('sg-review-close');if(close){close.addEventListener('click',closeReview);}
    var tabs=document.querySelectorAll('.sg-rev-tab');for(var i=0;i<tabs.length;i++){(function(t){t.addEventListener('click',function(){reviewState.state=t.getAttribute('data-state');for(var k=0;k<tabs.length;k++)tabs[k].classList.toggle('active',tabs[k]===t);refreshReview();});})(tabs[i]);}
    var cat=$('sg-review-cat'),min=$('sg-review-min'),max=$('sg-review-max'),end=$('sg-review-endp');
    [cat,min,max,end].forEach(function(inp){if(!inp)return;inp.addEventListener('change',readFilters);inp.addEventListener('input',function(){clearTimeout(inp._t);inp._t=setTimeout(readFilters,260);});});
  }
  function readFilters(){
    reviewState.category=($('sg-review-cat')||{}).value||'';
    reviewState.min=($('sg-review-min')||{}).value||'';
    reviewState.max=($('sg-review-max')||{}).value||'';
    reviewState.endpoint=($('sg-review-endp')||{}).value||'';
    refreshReview();
  }
  function openReview(){reviewOpen=true;$('sg-review').classList.add('open');$('sg-review-toggle').classList.add('on');refreshReview();}
  function closeReview(){reviewOpen=false;var rv=$('sg-review'),tg=$('sg-review-toggle');if(rv)rv.classList.remove('open');if(tg)tg.classList.remove('on');}
  function buildReviewUrl(){
    var q=['state='+encodeURIComponent(reviewState.state||'pending')];
    if(reviewState.category)q.push('category='+encodeURIComponent(reviewState.category));
    var mn=parseFloat(reviewState.min);if(!isNaN(mn))q.push('min_confidence='+mn);
    var mx=parseFloat(reviewState.max);if(!isNaN(mx))q.push('max_confidence='+mx);
    if(reviewState.endpoint)q.push('endpoint='+encodeURIComponent(reviewState.endpoint));
    return '/api/graph/review?'+q.join('&');
  }
  function refreshReview(){
    fetch(buildReviewUrl()).then(function(r){if(!r.ok)throw new Error('HTTP '+r.status);return r.json();}).then(function(data){
      var items=(data&&data.items)||[];reviewState.items=items;
      populateCategorySelect(items);
      var badge=$('sg-review-badge');if(badge)badge.textContent=String(items.length);
      var list=$('sg-review-list');if(!list)return;
      if(!items.length){list.innerHTML='<div class="sg-rev-empty">Nothing '+escapeHtml(reviewState.state||'pending')+' right now.</div>';return;}
      list.innerHTML=items.map(function(it){
        var color=REVIEW_CAT_COLORS[it.category]||REVIEW_CAT_COLORS.other;
        var conf=(it.confidence!=null?Number(it.confidence).toFixed(2):'-');
        var pendingState=(reviewState.state==='pending');
        return '<div class="sg-rev-item" data-rid="'+escapeAttr(it.id||'')+'">'+
          '<div class="sg-rev-edge"><strong>'+escapeHtml(it.subject_name||it.source||'')+'</strong> <span class="sg-rev-pred">'+escapeHtml(it.predicate||'')+'</span> '+escapeHtml(it.object_name||it.object_value||'')+'</div>'+
          '<div class="sg-rev-meta"><span class="sg-rev-cat" style="background:'+color+'"></span>'+escapeHtml(it.category||'other')+' &middot; conf '+conf+' &middot; '+escapeHtml(it.origin||'')+'</div>'+
          (pendingState?'<div class="sg-rev-actions">'+
            '<button data-act="rev-confirm" data-rid="'+escapeAttr(it.id||'')+'">Confirm</button>'+
            '<button class="sg-danger" data-act="rev-dismiss" data-rid="'+escapeAttr(it.id||'')+'">Dismiss</button>'+
          '</div>':'')+
        '</div>';
      }).join('');
      list.querySelectorAll('.sg-rev-item').forEach(function(row){
        row.addEventListener('click',function(ev){
          if(ev.target.closest('button'))return;
          // Open the inspector for this id by faking a selected edge from the item.
          var rid=row.getAttribute('data-rid');var it=null;for(var k=0;k<items.length;k++)if(items[k].id===rid){it=items[k];break;}
          if(!it)return;
          inspectorTarget={kind:'edge',data:{
            id:it.id,
            source:{id:it.source,name:it.subject_name},
            target:{id:it.target,name:it.object_name},
            predicate:it.predicate,category:it.category,confidence:it.confidence,locked:false
          }};
          renderInspector();
        });
      });
      list.querySelectorAll('[data-act="rev-confirm"]').forEach(function(b){
        b.addEventListener('click',function(ev){ev.stopPropagation();var rid=b.getAttribute('data-rid');b.disabled=true;postWrite('/api/graph/confirm',{relation_id:rid}).then(function(j){if(j&&j.ok)refreshAll();else b.disabled=false;}).catch(function(){b.disabled=false;});});
      });
      list.querySelectorAll('[data-act="rev-dismiss"]').forEach(function(b){
        b.addEventListener('click',function(ev){ev.stopPropagation();var rid=b.getAttribute('data-rid');b.disabled=true;postWrite('/api/graph/delete',{relation_id:rid}).then(function(j){if(j&&j.ok)refreshAll();else b.disabled=false;}).catch(function(){b.disabled=false;});});
      });
    }).catch(function(e){var list=$('sg-review-list');if(list)list.innerHTML='<div class="sg-rev-empty">Cannot load review queue ('+escapeHtml(e.message)+').</div>';});
  }
  function populateCategorySelect(items){
    var sel=$('sg-review-cat');if(!sel)return;
    var seen={};items.forEach(function(it){if(it.category)seen[it.category]=true;});
    var cats=Object.keys(seen).sort();
    var current=sel.value;
    sel.innerHTML='<option value="">all cats</option>'+cats.map(function(c){return '<option value="'+escapeAttr(c)+'"'+(c===current?' selected':'')+'>'+escapeHtml(c)+'</option>';}).join('');
  }
  function postWrite(url,body){
    return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}).then(function(r){return r.json();});
  }
  function refreshAll(){if(sageGraphCtl){sageGraphCtl.reload();}refreshReview();}
})();
