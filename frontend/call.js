(function(){
  var canvas=document.getElementById('orb');
  var ctx=canvas.getContext('2d');
  var W=280,H=280;
  canvas.width=W*devicePixelRatio;canvas.height=H*devicePixelRatio;
  canvas.style.width=W+'px';canvas.style.height=H+'px';
  ctx.scale(devicePixelRatio,devicePixelRatio);

  // ── Orb state ──
  // state: 'idle' | 'listening' | 'speaking'
  var orbState='idle';
  var energy=0; // 0-1, drives scale/glow when speaking

  // Orb palette — warm monochrome, one ring each deeper
  var CX=140,CY=140;
  var COLORS=['#3a3530','#4a443c','#5c574f','#8d867c','#cfcbc4'];

  function lerp(a,b,t){return a+(b-a)*t;}

  var t=0;
  function drawOrb(){
    ctx.clearRect(0,0,W,H);

    var breathe;
    if(orbState==='idle'){
      breathe=0.92+0.08*Math.sin(t*0.6);
    } else if(orbState==='listening'){
      breathe=0.88+0.12*Math.sin(t*2.2);
    } else {
      // speaking: energy drives the pulse
      breathe=0.88+0.22*energy+0.04*Math.sin(t*4);
    }

    var baseR=88*breathe;

    // Outer glow rings (fading outward)
    for(var i=4;i>=0;i--){
      var r=baseR*(1+i*0.13);
      var alpha=orbState==='speaking'
        ? lerp(0.04,0.14,energy)/(i+1)
        : 0.04/(i*0.5+1);
      ctx.beginPath();
      ctx.arc(CX,CY,r,0,Math.PI*2);
      ctx.fillStyle=COLORS[i];
      ctx.globalAlpha=alpha;
      ctx.fill();
    }

    // Core gradient
    ctx.globalAlpha=1;
    var grad=ctx.createRadialGradient(CX-12,CY-12,4,CX,CY,baseR);
    grad.addColorStop(0,'#5c574f');
    grad.addColorStop(0.4,'#33302c');
    grad.addColorStop(1,'#1a1816');
    ctx.beginPath();
    ctx.arc(CX,CY,baseR,0,Math.PI*2);
    ctx.fillStyle=grad;
    ctx.fill();

    // Subtle highlight
    ctx.globalAlpha=orbState==='speaking'?lerp(0.12,0.28,energy):0.10;
    var hgrad=ctx.createRadialGradient(CX-20,CY-28,2,CX,CY,baseR);
    hgrad.addColorStop(0,'#cfcbc4');
    hgrad.addColorStop(1,'transparent');
    ctx.beginPath();
    ctx.arc(CX,CY,baseR,0,Math.PI*2);
    ctx.fillStyle=hgrad;
    ctx.fill();

    ctx.globalAlpha=1;
  }

  // ── AudioContext for speaking energy ──
  var audioCtx=null,analyser=null,dataArr=null;
  function initAudio(){
    if(audioCtx)return;
    audioCtx=new (window.AudioContext||window.webkitAudioContext)();
    analyser=audioCtx.createAnalyser();
    analyser.fftSize=128;
    dataArr=new Uint8Array(analyser.frequencyBinCount);
  }
  function connectAudio(audioEl){
    initAudio();
    var src=audioCtx.createMediaElementSource(audioEl);
    src.connect(analyser);
    analyser.connect(audioCtx.destination);
    return src;
  }
  function readEnergy(){
    if(!analyser)return 0;
    analyser.getByteTimeDomainData(dataArr);
    var sum=0;
    for(var i=0;i<dataArr.length;i++){var v=(dataArr[i]-128)/128;sum+=v*v;}
    return Math.min(1,Math.sqrt(sum/dataArr.length)*4);
  }

  // ── Animation loop ──
  var lastE=0;
  function frame(){
    t+=0.016;
    if(orbState==='speaking'){
      var raw=readEnergy();
      energy=lerp(lastE,raw,0.25);lastE=energy;
    } else {
      energy=lerp(lastE,0,0.08);lastE=energy;
    }
    drawOrb();
    requestAnimationFrame(frame);
  }
  frame();

  // ── TTS queue ──
  var ttsQueue=[],ttsPlaying=false;
  var currentAudio=null,currentSrc=null;
  function ttsDeliver(slot,blob){
    ttsSlots[slot]=blob;
    while(ttsSlots[ttsSlotDrain]!==undefined){
      var b=ttsSlots[ttsSlotDrain];
      delete ttsSlots[ttsSlotDrain];
      ttsSlotDrain++;
      if(b)ttsQueue.push(b);
      if(!ttsPlaying)ttsNext();
    }
  }
  function ttsNext(){
    if(!ttsQueue.length){ttsPlaying=false;setOrbState('idle');return;}
    ttsPlaying=true;setOrbState('speaking');
    var blob=ttsQueue.shift();
    var url=URL.createObjectURL(blob);
    var a=new Audio(url);
    a.crossOrigin='anonymous';
    currentAudio=a;
    if(currentSrc){try{currentSrc.disconnect();}catch(e){}currentSrc=null;}
    try{currentSrc=connectAudio(a);}catch(e){}
    a.onended=function(){URL.revokeObjectURL(url);currentAudio=null;ttsNext();};
    a.onerror=function(){URL.revokeObjectURL(url);currentAudio=null;ttsNext();};
    a.play().catch(function(){ttsNext();});
  }
  function ttsFlush(){
    ttsGen++;
    ttsQueue=[];
    ttsSlots={};
    ttsSlotNext=0;
    ttsSlotDrain=0;
    if(currentAudio){try{currentAudio.pause();}catch(e){}currentAudio=null;}
    if(currentSrc){try{currentSrc.disconnect();}catch(e){}currentSrc=null;}
    ttsPlaying=false;
  }
  function stripMd(t){
    return t
      .replace(/\*\*([^*]+)\*\*/g,'$1')  // **bold**
      .replace(/\*([^*]+)\*/g,'$1')       // *italic*
      .replace(/`([^`]+)`/g,'$1')         // `code`
      .replace(/#+\s*/g,'')               // # headings
      .replace(/\[([^\]]+)\]\([^)]+\)/g,'$1') // [link](url)
      .replace(/_{1,2}([^_]+)_{1,2}/g,'$1'); // _underline_
  }
  function ttsSynth(text){
    var clean=stripMd(text).trim();
    if(!clean)return;
    var slot=ttsSlotNext++;
    var gen=ttsGen;
    fetch('/api/voice/tts',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:clean})})
      .then(function(r){return r.ok?r.blob():null;})
      .then(function(b){
        if(gen!==ttsGen)return;
        ttsDeliver(slot,b&&b.size>100?b:null);
      })
      .catch(function(){
        if(gen!==ttsGen)return;
        ttsDeliver(slot,null);
      });
  }

  // sentence-stream watcher (same logic as original plan)
  var SENT=/[.!?](?:\s|$)/g;
  var spokenAt=0;
  function speakNew(acc){
    var rest=acc.slice(spokenAt);SENT.lastIndex=0;
    var m,last=0;while((m=SENT.exec(rest))!==null)last=m.index+m[0].length;
    if(last>0){var chunk=rest.slice(0,last).trim();spokenAt+=last;if(chunk)ttsSynth(chunk);}
  }

  // ── Status label ──
  var label=document.getElementById('status-label');
  function setOrbState(s){
    orbState=s;
    label.className=s==='idle'?'':s;
    label.textContent=s==='idle'?'Hold to talk':s==='listening'?'Listening…':'Speaking…';
  }
  // Errors must be visible — a silent failure reads as a dead button.
  function showErr(msg){orbState='idle';label.className='err';label.textContent=msg;}

  // ── Hold-to-talk ──
  var btn=document.getElementById('mic-btn');
  var recorder=null,chunks=[];
  var micOpening=false;        // Defect 1: getUserMedia in flight
  var recordingIntent=false;   // Defect 1: user still holding
  var ttsGen=0;                // Defect 3: ++ on flush, checked before enqueue
  var ttsSlots={};             // Defect 2: slot index → blob (ordered)
  var ttsSlotNext=0;           // Defect 2: next slot to assign
  var ttsSlotDrain=0;          // Defect 2: next slot to play

  function startRec(ev){
    ev.preventDefault();
    if(micOpening||(recorder&&recorder.state==='recording'))return; // Defect 1: re-entrant guard
    ttsFlush(); // always flush — interrupt stale speech and chat stream
    if(!navigator.mediaDevices||!navigator.mediaDevices.getUserMedia){
      showErr('Mic needs HTTPS or localhost — open via localhost:6969 or tailscale HTTPS');
      return;
    }
    try{initAudio();if(audioCtx.state==='suspended')audioCtx.resume();}catch(e){}
    recordingIntent=true;micOpening=true;
    chunks=[];
    navigator.mediaDevices.getUserMedia({audio:true}).then(function(stream){
      micOpening=false;
      if(!recordingIntent){
        stream.getTracks().forEach(function(t){t.stop();});
        setOrbState('idle');
        return;
      }
      recorder=new MediaRecorder(stream);
      recorder.ondataavailable=function(e){if(e.data.size>0)chunks.push(e.data);};
      recorder.onstop=function(){
        stream.getTracks().forEach(function(t){t.stop();});
        var blob=new Blob(chunks,{type:recorder.mimeType||'audio/webm'});
        if(blob.size<500){setOrbState('idle');return;}
        setOrbState('idle');
        label.textContent='Thinking…';
        var fd=new FormData();fd.append('file',blob,'audio.webm');
        fetch('/api/voice/stt',{method:'POST',body:fd})
          .then(function(r){return r.ok?r.json():{transcript:''};})
          .then(function(j){
            var text=(j&&j.transcript||'').trim();
            if(!text){setOrbState('idle');return;}
            spokenAt=0;
            var respGen=ttsGen; // Defect 3: capture gen at chat start
            fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text})})
              .then(function(r){
                if(!r.ok)throw new Error('HTTP '+r.status);
                if(!r.body||!r.body.getReader){return r.text().then(function(t){ttsSynth(t);});}
                var reader=r.body.getReader(),decoder=new TextDecoder();
                var acc='',sbuf='',inFrame=false;
                var RS=String.fromCharCode(30);
                function feed(chunk){sbuf+=chunk;var out='';while(true){var i=sbuf.indexOf(RS);if(i===-1){if(!inFrame){out+=sbuf;sbuf='';}break;}if(!inFrame){out+=sbuf.slice(0,i);sbuf=sbuf.slice(i+1);inFrame=true;}else{sbuf=sbuf.slice(i+1);inFrame=false;}}return out;}
                return (function pump(){return reader.read().then(function(res){
                  if(respGen!==ttsGen)return; // Defect 3: cancelled by later flush
                  if(res.done){var tail=acc.slice(spokenAt).trim();if(tail)ttsSynth(tail);return;}
                  var text=feed(decoder.decode(res.value,{stream:true}));
                  if(text){acc+=text;speakNew(acc);}
                  return pump();
                });})();
              }).catch(function(){setOrbState('idle');});
          }).catch(function(){setOrbState('idle');});
      };
      recorder.start();
      btn.classList.add('recording');
      setOrbState('listening');
    }).catch(function(e){
      micOpening=false;
      showErr(e&&e.name==='NotAllowedError'?'Mic permission denied — allow it in the address bar':'Mic unavailable: '+((e&&e.name)||'unknown'));
    });
  }

  function stopRec(){
    recordingIntent=false; // Defect 1: gesture ended, don't start if stream still pending
    if(recorder&&recorder.state==='recording'){recorder.stop();}
    btn.classList.remove('recording');
  }

  btn.addEventListener('mousedown',startRec);
  btn.addEventListener('touchstart',startRec,{passive:false});
  btn.addEventListener('keydown',function(e){if(e.key===' '||e.key==='Enter'){e.preventDefault();startRec(e);}});
  btn.addEventListener('keyup',function(e){if(e.key===' '||e.key==='Enter')stopRec();});
  document.addEventListener('mouseup',stopRec);
  document.addEventListener('touchend',stopRec);
  document.addEventListener('touchcancel',stopRec);

  // Verify voice is available, else redirect home
  fetch('/api/voice/status').then(function(r){return r.ok?r.json():null;}).then(function(d){
    if(!d||!d.enabled)window.location.href='/';
  }).catch(function(){window.location.href='/';});
})();
