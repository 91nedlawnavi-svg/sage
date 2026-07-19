/* === Sage people-graph core (brain-authored) === */
/* Sage people-graph - brain-authored graph layer (warm monochrome).
   Classic browser script. Requires global d3 (v7). No build step.
   Exposes window.initSageGraph(opts) -> controller { reload, refit, stop, resume }. */
(function (global) {
  "use strict";

  var CATEGORY_COLORS = {
    family: "#c98a6f",
    friend: "#8ca37e",
    romantic: "#c08aa0",
    colleague: "#7f93a8",
    acquaintance: "#9a8d86",
    creator: "#c2a15e",
    other: "#857c73"
  };
  var CATEGORY_ORDER = ["family", "friend", "romantic", "colleague", "acquaintance", "creator", "other"];

  function catColor(c) { return CATEGORY_COLORS[c] || CATEGORY_COLORS.other; }
  function clamp(v, a, b) { return Math.max(a, Math.min(b, v)); }
  function esc(s) { return String(s == null ? "" : s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;"); }

  function initSageGraph(opts) {
    var d3 = global.d3;
    if (!d3) { return null; }

    var svgEl = opts.svg;
    var chipsEl = opts.chips;
    var tooltipEl = opts.tooltip;
    var getData = opts.getData;
    var egoId = opts.egoId || "person:elliot";
    var onSelect = typeof opts.onSelect === "function" ? opts.onSelect : null;

    var svg = d3.select(svgEl);
    svg.selectAll("*").remove();

    var defs = svg.append("defs");
    var zoomG = svg.append("g").attr("class", "sg-zoom");
    var linkLayer = zoomG.append("g");
    var lockLayer = zoomG.append("g");
    var nodeLayer = zoomG.append("g").attr("class", "sg-node-layer");

    var nodes = [], links = [];
    var active = {};
    var simulation = null;
    var W = 1, H = 1;
    var nodeSel, linkSel, lockSel;

    var zoom = d3.zoom().scaleExtent([0.4, 2.5]).on("zoom", function (ev) {
      zoomG.attr("transform", ev.transform);
      nodeLayer.classed("far", ev.transform.k < 0.75);
    });
    svg.call(zoom).on("dblclick.zoom", null);

    var degree = {};
    function nodeRadius(d) {
      var base = 4.5 + Math.sqrt(degree[d.id] || 0) * 2.6;
      if (d.id === egoId) base = Math.max(base, 11);
      return clamp(base, 4.5, 17);
    }

    function measure(cb, tries) {
      tries = tries || 0;
      requestAnimationFrame(function () {
        requestAnimationFrame(function () {
          var w = svgEl.clientWidth, h = svgEl.clientHeight;
          if ((w <= 1 || h <= 1) && tries < 30) { measure(cb, tries + 1); return; }
          W = w > 1 ? w : 360; H = h > 1 ? h : 460;
          cb();
        });
      });
    }

    function shapeFor(sel) {
      sel.each(function (d) {
        var g = d3.select(this);
        var r = nodeRadius(d);
        if (d.id === egoId) {
          g.append("circle").attr("class", "sg-ego-ring").attr("r", r + 4);
        }
        g.append("circle").attr("class", "sg-shape").attr("r", r);
        g.append("text").attr("class", "sg-label").attr("y", r + 12).attr("text-anchor", "middle").text(d.name || d.id);
      });
    }

    function neighborsOf(id) {
      var s = {}; s[id] = true;
      links.forEach(function (l) {
        var sid = l.source.id || l.source, tid = l.target.id || l.target;
        if (sid === id) s[tid] = true;
        if (tid === id) s[sid] = true;
      });
      return s;
    }

    function dimExcept(keepNodes, keepLink) {
      nodeSel.style("opacity", function (d) { return keepNodes[d.id] ? 1 : 0.12; });
      linkSel.style("opacity", function (l) { return keepLink(l) ? 0.9 : 0.06; });
      lockSel.style("opacity", function (l) { return keepLink(l) ? 1 : 0.06; });
    }
    function undim() {
      nodeSel.style("opacity", 1);
      linkSel.style("opacity", null);
      lockSel.style("opacity", null);
    }

    function visible(l) { return active[l.category] !== false; }

    function applyFilter() {
      var vis = {};
      links.forEach(function (l) {
        if (visible(l)) { vis[l.source.id || l.source] = true; vis[l.target.id || l.target] = true; }
      });
      nodeSel.attr("display", function (d) { return vis[d.id] ? null : "none"; });
      linkSel.attr("display", function (l) { return visible(l) ? null : "none"; });
      lockSel.attr("display", function (l) { return (visible(l) && l.locked) ? null : "none"; });
    }

    function buildChips(cats) {
      chipsEl.innerHTML = "";
      cats.forEach(function (c) {
        var b = document.createElement("button");
        b.className = "sg-chip";
        b.style.setProperty("--cc", catColor(c));
        b.textContent = c;
        b.setAttribute("data-cat", c);
        if (active[c] === false) b.classList.add("off");
        b.addEventListener("click", function () {
          active[c] = (active[c] === false);
          b.classList.toggle("off", active[c] === false);
          applyFilter();
        });
        chipsEl.appendChild(b);
      });
    }

    function tooltipHtml(kind, d) {
      if (kind === "node") {
        var facts = d.facts || [];
        var lines = facts.map(function (f) {
          return '<div class="sg-tip-fact"><span>' + esc(f.predicate) + '</span> ' + esc(f.value) + (f.locked ? ' <span class="sg-tip-lock">locked</span>' : '') + '</div>';
        }).join("");
        return '<div class="sg-tip-title">' + esc(d.name || d.id) + ' <span class="sg-tip-type">' + esc(d.type || "") + '</span></div>' + (lines || '<div class="sg-tip-empty">no facts</div>');
      }
      var l = d;
      return '<div class="sg-tip-title">' + esc(l.source.name || l.source.id) + ' &rarr; ' + esc(l.target.name || l.target.id) + '</div>' +
        '<div class="sg-tip-row"><span>predicate</span> ' + esc(l.predicate) + '</div>' +
        '<div class="sg-tip-row"><span>category</span> ' + esc(l.category) + '</div>' +
        '<div class="sg-tip-row"><span>confidence</span> ' + (l.confidence != null ? Number(l.confidence).toFixed(2) : "-") + '</div>' +
        (l.locked ? '<div class="sg-tip-row sg-tip-lock">locked</div>' : '');
    }
    function showTip(html, ev) { tooltipEl.innerHTML = html; tooltipEl.style.display = "block"; moveTip(ev); }
    function moveTip(ev) { tooltipEl.style.left = (ev.clientX + 14) + "px"; tooltipEl.style.top = (ev.clientY + 14) + "px"; }
    function hideTip() { tooltipEl.style.display = "none"; }

    function linkPath(l) {
      var s = l.source, t = l.target;
      var dx = t.x - s.x, dy = t.y - s.y;
      var dist = Math.sqrt(dx * dx + dy * dy) || 1;
      var ux = dx / dist, uy = dy / dist;
      var sr = nodeRadius(s) + 1.5, tr = nodeRadius(t) + 1.5;
      var sx = s.x + ux * sr, sy = s.y + uy * sr;
      var ex = t.x - ux * tr, ey = t.y - uy * tr;
      if (!l._curve) {
        l._mx = (sx + ex) / 2; l._my = (sy + ey) / 2;
        return "M" + sx + "," + sy + "L" + ex + "," + ey;
      }
      var mx = (sx + ex) / 2, my = (sy + ey) / 2;
      var cx = mx + (-uy) * l._curve, cy = my + (ux) * l._curve;
      l._mx = 0.25 * sx + 0.5 * cx + 0.25 * ex;
      l._my = 0.25 * sy + 0.5 * cy + 0.25 * ey;
      return "M" + sx + "," + sy + "Q" + cx + "," + cy + " " + ex + "," + ey;
    }

    function ticked() {
      linkSel.attr("d", linkPath);
      lockSel.attr("transform", function (l) { return "translate(" + (l._mx || 0) + "," + (l._my || 0) + ")"; });
      nodeSel.attr("transform", function (d) { return "translate(" + d.x + "," + d.y + ")"; });
    }

    function fitToView() {
      if (!nodes.length) return;
      var minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      nodes.forEach(function (d) {
        if (d.x < minX) minX = d.x; if (d.x > maxX) maxX = d.x;
        if (d.y < minY) minY = d.y; if (d.y > maxY) maxY = d.y;
      });
      var pad = 46;
      var bw = Math.max(1, maxX - minX), bh = Math.max(1, maxY - minY);
      var scale = clamp(Math.min((W - 2 * pad) / bw, (H - 2 * pad) / bh), 0.4, 2.0);
      var cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
      var tx = W / 2 - scale * cx, ty = H / 2 - scale * cy;
      svg.transition().duration(450).call(zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
    }

    function assignCurves() {
      var groups = {};
      links.forEach(function (l) {
        var a = (l.source.id || l.source), b = (l.target.id || l.target);
        var key = [a, b].sort().join("|");
        (groups[key] = groups[key] || []).push(l);
      });
      Object.keys(groups).forEach(function (k) {
        var g = groups[k], n = g.length;
        g.forEach(function (l, i) { l._curve = n === 1 ? 0 : (i - (n - 1) / 2) * 26; });
      });
    }

    function build() {
      var cats = {};
      degree = {};
      links.forEach(function (l) {
        cats[l.category] = true;
        var a = l.source.id || l.source, b = l.target.id || l.target;
        degree[a] = (degree[a] || 0) + 1;
        degree[b] = (degree[b] || 0) + 1;
      });
      var catList = CATEGORY_ORDER.filter(function (c) { return cats[c]; });
      Object.keys(cats).forEach(function (c) { if (catList.indexOf(c) < 0) catList.push(c); });
      catList.forEach(function (c) { if (active[c] === undefined) active[c] = true; });

      buildChips(catList);
      assignCurves();

      linkSel = linkLayer.selectAll("path").data(links).join("path")
        .attr("class", "sg-link")
        .attr("stroke", function (l) { return catColor(l.category); })
        .attr("stroke-width", function (l) { return l.locked ? 2 : 1.2; })
        .attr("fill", "none")
        .style("cursor", onSelect ? "pointer" : null)
        .on("mouseover", function (ev, l) { showTip(tooltipHtml("edge", l), ev); var k = {}; k[l.source.id] = true; k[l.target.id] = true; dimExcept(k, function (x) { return x === l; }); })
        .on("mousemove", function (ev) { moveTip(ev); })
        .on("mouseout", function () { hideTip(); undim(); })
        .on("click", function (ev, l) { if (onSelect) { ev.stopPropagation(); onSelect("edge", l); } });

      lockSel = lockLayer.selectAll("circle").data(links.filter(function (l) { return l.locked; })).join("circle")
        .attr("class", "sg-lock").attr("r", 2);

      nodeSel = nodeLayer.selectAll("g.sg-node").data(nodes).join(function (enter) {
        var g = enter.append("g").attr("class", "sg-node");
        shapeFor(g);
        return g;
      });

      nodeSel.call(d3.drag()
        .on("start", function (ev, d) { if (!ev.active) simulation.alphaTarget(0.25).restart(); d.fx = d.x; d.fy = d.y; if (ev.sourceEvent) ev.sourceEvent.stopPropagation(); })
        .on("drag", function (ev, d) { d.fx = ev.x; d.fy = ev.y; })
        .on("end", function (ev, d) { if (!ev.active) simulation.alphaTarget(0); }));

      nodeSel.on("mouseover", function (ev, d) { showTip(tooltipHtml("node", d), ev); var nb = neighborsOf(d.id); dimExcept(nb, function (l) { return (l.source.id || l.source) === d.id || (l.target.id || l.target) === d.id; }); })
        .on("mousemove", function (ev) { moveTip(ev); })
        .on("mouseout", function () { hideTip(); undim(); })
        .on("click", function (ev, d) { if (onSelect) { ev.stopPropagation(); onSelect("node", d); } })
        .on("dblclick", function (ev, d) { d.fx = null; d.fy = null; if (simulation) simulation.alpha(0.3).restart(); });

      simulation = d3.forceSimulation(nodes)
        .force("link", d3.forceLink(links).id(function (d) { return d.id; }).distance(96).strength(0.55))
        .force("charge", d3.forceManyBody().strength(-340))
        .force("center", d3.forceCenter(W / 2, H / 2))
        .force("x", d3.forceX(W / 2).strength(0.07))
        .force("y", d3.forceY(H / 2).strength(0.07))
        .force("collide", d3.forceCollide(38))
        .on("tick", ticked)
        .on("end", fitToView);

      applyFilter();
      setTimeout(fitToView, 1400);
    }

    function load() {
      return getData().then(function (data) {
        var rawNodes = (data && data.nodes) || [];
        var rawEdges = (data && data.edges) || [];
        var byId = {};
        nodes = rawNodes.map(function (n) { var o = { id: n.id, type: n.type, name: n.name, facts: n.facts || [] }; byId[o.id] = o; return o; });
        links = rawEdges.map(function (e) {
          return { source: e.source, target: e.target, predicate: e.predicate, category: e.category || "other", confidence: e.confidence, locked: !!e.locked };
        }).filter(function (l) { return byId[l.source] && byId[l.target]; });
        measure(build);
      });
    }

    if (global.ResizeObserver) {
      var ro = new ResizeObserver(function () {
        var w = svgEl.clientWidth, h = svgEl.clientHeight;
        if (w > 1 && h > 1 && simulation) {
          W = w; H = h;
          simulation.force("center", d3.forceCenter(W / 2, H / 2));
          simulation.force("x", d3.forceX(W / 2).strength(0.07));
          simulation.force("y", d3.forceY(H / 2).strength(0.07));
          simulation.alpha(0.2).restart();
          setTimeout(fitToView, 420);
        }
      });
      ro.observe(svgEl);
    }

    return {
      reload: load,
      refit: fitToView,
      stop: function () { if (simulation) simulation.stop(); },
      resume: function () { if (simulation && simulation.alpha() < 0.02) { simulation.alpha(0.05).restart(); } },
      getNodes: function () { return nodes; },
      getLinks: function () { return links; }
    };
  }

  global.initSageGraph = initSageGraph;
})(window);
