(function () {
  const data = window.TRACE_DATA || { source: 'unknown', destinations: [] };
  const svg = document.getElementById('viz');
  const viewSelect = document.getElementById('viewSelect');
  const protocolSelect = document.getElementById('protocolSelect');
  const destinationSelect = document.getElementById('destinationSelect');
  const unknownToggle = document.getElementById('unknownToggle');
  const aggregateToggle = document.getElementById('aggregateToggle');
  const searchInput = document.getElementById('searchInput');
  const summaryCards = document.getElementById('summaryCards');
  const detailList = document.getElementById('detailList');
  const rawTable = document.getElementById('rawTable');
  const protocolTable = document.getElementById('protocolTable');
  const sharedTable = document.getElementById('sharedTable');
  const rttChart = document.getElementById('rttChart');
  const lossChart = document.getElementById('lossChart');
  const pageInfo = document.getElementById('pageInfo');
  const prevPage = document.getElementById('prevPage');
  const nextPage = document.getElementById('nextPage');
  const viewTitle = document.getElementById('viewTitle');
  const vizOverlay = document.getElementById('vizOverlay');

  const COLORS = { UDP: '#63b3ff', TCP: '#ff8f6b', ICMP: '#7de2a8', ALL: '#d9dfea', UNKNOWN: '#7d859a' };
  const PAGE_SIZE = 80;
  let selectedInfo = null;
  let currentRawRows = [];
  let currentPage = 0;

  function protocolColor(protocol) {
    return COLORS[protocol] || COLORS.ALL;
  }

  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }
  function mean(nums) { return nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : 0; }
  function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }
  function fmtMs(v) { return Number.isFinite(v) ? `${v.toFixed(1)} ms` : '–'; }
  function escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function buildDestinationOptions() {
    const all = [...data.destinations];
    for (const d of all) {
      const opt = document.createElement('option');
      opt.value = d.target;
      opt.textContent = `${d.target}${d.input_network ? ` (${d.input_network})` : ''}`;
      destinationSelect.appendChild(opt);
    }
  }

  function applyDestinationSearch() {
    const q = searchInput.value.trim().toLowerCase();
    const current = destinationSelect.value;
    destinationSelect.innerHTML = '<option value="ALL">All destinations</option>';
    for (const d of data.destinations) {
      const label = `${d.target} ${d.input_network || ''}`.toLowerCase();
      if (q && !label.includes(q)) continue;
      const opt = document.createElement('option');
      opt.value = d.target;
      opt.textContent = `${d.target}${d.input_network ? ` (${d.input_network})` : ''}`;
      destinationSelect.appendChild(opt);
    }
    if ([...destinationSelect.options].some(o => o.value === current)) destinationSelect.value = current;
    else destinationSelect.value = 'ALL';
    rerender();
  }

  function filteredDestinations() {
    const target = destinationSelect.value;
    if (target === 'ALL') return data.destinations;
    return data.destinations.filter(d => d.target === target);
  }

  function filterProbes(probes, protocol) {
    return probes.filter(p => protocol === 'ALL' || p.protocol === protocol);
  }

  function groupKeyForNode(node, ttl, keptSet) {
    if (keptSet.has(node.id)) return node.id;
    return `OTHER@TTL${ttl}`;
  }

  function aggregate() {
    const protocol = protocolSelect.value;
    const showUnknown = unknownToggle.checked;
    const destinations = filteredDestinations();
    const nodeMap = new Map();
    const edgeMap = new Map();
    const pathSummaries = [];
    const rawRows = [];

    for (const dst of destinations) {
      const probes = filterProbes(dst.probes || [], protocol);
      const ttlMap = new Map();
      for (const p of probes) {
        const ttl = Number(p.ttl);
        if (!ttlMap.has(ttl)) ttlMap.set(ttl, []);
        ttlMap.get(ttl).push(p);
        rawRows.push({ target: dst.target, ...p });
      }

      const orderedTtls = [...ttlMap.keys()].sort((a, b) => a - b);
      const perDestPath = [];

      for (const ttl of orderedTtls) {
        const bucket = ttlMap.get(ttl);
        const groups = new Map();
        for (const p of bucket) {
          if ((!showUnknown) && !p.success) continue;
          const key = p.success && p.reply_ip ? p.reply_ip : `unknown@${dst.target}@${ttl}`;
          if (!groups.has(key)) groups.set(key, []);
          groups.get(key).push(p);
        }

        const nodesThisTtl = [];
        for (const [key, arr] of groups.entries()) {
          const successArr = arr.filter(x => x.success && x.reply_ip);
          const sample = successArr[0] || arr[0];
          const nodeId = sample.success && sample.reply_ip ? sample.reply_ip : key;
          const isUnknown = !(sample.success && sample.reply_ip);
          if (!nodeMap.has(nodeId)) {
            nodeMap.set(nodeId, {
              id: nodeId,
              ip: sample.reply_ip || '*',
              name: sample.reply_name || '',
              city: sample.city || '',
              country: sample.country || '',
              lat: sample.lat,
              lng: sample.lng,
              ttlSet: new Set([ttl]),
              destinations: new Set([dst.target]),
              rtts: arr.filter(x => x.success && x.rtt_ms != null).map(x => Number(x.rtt_ms)),
              sent: arr.length,
              ok: arr.filter(x => x.success).length,
              reached: arr.some(x => x.reached_destination),
              unknown: isUnknown,
              protocolHits: new Map(arr.map(x => [x.protocol, 1]))
            });
          } else {
            const n = nodeMap.get(nodeId);
            n.ttlSet.add(ttl);
            n.destinations.add(dst.target);
            n.rtts.push(...arr.filter(x => x.success && x.rtt_ms != null).map(x => Number(x.rtt_ms)));
            n.sent += arr.length;
            n.ok += arr.filter(x => x.success).length;
            n.reached = n.reached || arr.some(x => x.reached_destination);
            if ((!n.lat || !n.lng) && sample.lat != null && sample.lng != null) {
              n.lat = sample.lat; n.lng = sample.lng; n.city = sample.city || n.city; n.country = sample.country || n.country;
            }
          }
          nodesThisTtl.push(nodeId);
        }
        perDestPath.push({ ttl, nodes: nodesThisTtl });
      }

      for (let i = 0; i < perDestPath.length - 1; i++) {
        const a = perDestPath[i];
        const b = perDestPath[i + 1];
        for (const src of a.nodes) {
          for (const dstNode of b.nodes) {
            const key = `${src}=>${dstNode}`;
            if (!edgeMap.has(key)) {
              edgeMap.set(key, {
                key,
                source: src,
                target: dstNode,
                destinations: new Set(),
                rtts: [],
                sent: 0,
                ok: 0,
                srcTTL: a.ttl,
                dstTTL: b.ttl
              });
            }
            const e = edgeMap.get(key);
            e.destinations.add(dst.target);
            const nextHopProbes = (ttlMap.get(b.ttl) || []).filter(p => protocol === 'ALL' || p.protocol === protocol);
            e.rtts.push(...nextHopProbes.filter(x => x.success && x.rtt_ms != null).map(x => Number(x.rtt_ms)));
            e.sent += nextHopProbes.length;
            e.ok += nextHopProbes.filter(x => x.success).length;
          }
        }
      }

      pathSummaries.push({ target: dst.target, path: perDestPath });
    }

    const nodes = [...nodeMap.values()].map(n => ({
      ...n,
      avgRtt: mean(n.rtts),
      lossRate: n.sent ? 1 - (n.ok / n.sent) : 0,
      ttlMin: Math.min(...n.ttlSet),
      ttlMax: Math.max(...n.ttlSet),
      sharedCount: n.destinations.size
    }));

    const edges = [...edgeMap.values()].map(e => ({
      ...e,
      avgRtt: mean(e.rtts),
      lossRate: e.sent ? 1 - (e.ok / e.sent) : 0,
      sharedCount: e.destinations.size
    }));

    return { nodes, edges, pathSummaries, protocol, destinations, rawRows };
  }

  function computeMetrics(agg) {
    const protocolGroups = { UDP: [], TCP: [], ICMP: [] };
    for (const d of filteredDestinations()) {
      for (const p of d.probes || []) {
        if (protocolGroups[p.protocol]) protocolGroups[p.protocol].push(p);
      }
    }
    const avgRttByProto = {};
    const lossByProto = {};
    const reachedByProto = {};
    for (const proto of ['UDP', 'TCP', 'ICMP']) {
      const arr = protocolGroups[proto];
      avgRttByProto[proto] = mean(arr.filter(x => x.success && x.rtt_ms != null).map(x => Number(x.rtt_ms)));
      lossByProto[proto] = arr.length ? 1 - (arr.filter(x => x.success).length / arr.length) : 0;
      reachedByProto[proto] = new Set(filteredDestinations().filter(d => (d.probes || []).some(p => p.protocol === proto && p.reached_destination)).map(d => d.target)).size;
    }
    const mostSharedNode = agg.nodes.slice().sort((a, b) => b.sharedCount - a.sharedCount)[0] || null;
    const widestBranch = agg.pathSummaries.reduce((mx, p) => Math.max(mx, ...p.path.map(step => step.nodes.length), 0), 0);
    const maxTTL = agg.nodes.reduce((mx, n) => Math.max(mx, n.ttlMax || 0), 0);
    return {
      destinationCount: filteredDestinations().length,
      nodeCount: agg.nodes.length,
      edgeCount: agg.edges.length,
      maxTTL,
      avgRttByProto,
      lossByProto,
      reachedByProto,
      mostSharedNode,
      widestBranch
    };
  }

  function renderCards(metrics) {
    const cards = [
      ['Destinations', metrics.destinationCount],
      ['Nodes', metrics.nodeCount],
      ['Edges', metrics.edgeCount],
      ['Max TTL observed', metrics.maxTTL],
      ['Widest branch', metrics.widestBranch],
      ['Source', typeof data.source === 'string' ? data.source : (data.source.ip || 'unknown')],
    ];
    summaryCards.innerHTML = cards.map(([k, v]) => `<div class="card"><div class="k">${escapeHtml(k)}</div><div class="v">${escapeHtml(v)}</div></div>`).join('');
  }

  function renderBars(el, title, values, formatter, maxOverride) {
    const max = maxOverride ?? Math.max(...Object.values(values), 1);
    el.innerHTML = `<div class="chart-title">${escapeHtml(title)}</div>` + ['UDP', 'TCP', 'ICMP'].map(proto => {
      const val = values[proto] || 0;
      const pct = max ? (val / max) * 100 : 0;
      return `<div class="bar-row">
        <div>${proto}</div>
        <div class="bar-track"><div class="bar-fill" style="width:${pct}%;background:${protocolColor(proto)}"></div></div>
        <div>${escapeHtml(formatter(val))}</div>
      </div>`;
    }).join('');
  }

  function renderProtocolTable(metrics) {
    protocolTable.innerHTML = ['UDP', 'TCP', 'ICMP'].map(proto => `
      <tr>
        <td>${proto}</td>
        <td>${metrics.avgRttByProto[proto].toFixed(1)} ms</td>
        <td>${(metrics.lossByProto[proto] * 100).toFixed(1)}%</td>
        <td>${metrics.reachedByProto[proto]}/${metrics.destinationCount}</td>
      </tr>`).join('');
  }

  function renderSharedTable(agg) {
    const rows = agg.nodes
      .filter(n => !n.unknown)
      .sort((a, b) => b.sharedCount - a.sharedCount || a.ttlMin - b.ttlMin)
      .slice(0, 12)
      .map(n => `
        <tr>
          <td>${escapeHtml(n.ip)}</td>
          <td>${n.ttlMin}</td>
          <td>${n.sharedCount}</td>
          <td>${fmtMs(n.avgRtt)}</td>
        </tr>`).join('');
    sharedTable.innerHTML = rows || '<tr><td colspan="4">No hops to show.</td></tr>';
  }

  function updateDetails(obj) {
    const items = [];
    if (!obj) {
      items.push(['Hint', 'Click a node or link in the plot to inspect it.']);
    } else if (obj.type === 'node') {
      items.push(['Node IP', obj.ip]);
      items.push(['Shared by destinations', obj.sharedCount]);
      items.push(['TTL range', `${obj.ttlMin} – ${obj.ttlMax}`]);
      items.push(['Average RTT', fmtMs(obj.avgRtt)]);
      items.push(['Loss', `${(obj.lossRate * 100).toFixed(1)}%`]);
      items.push(['Reached destination node', obj.reached ? 'Yes' : 'No']);
    } else if (obj.type === 'edge') {
      items.push(['Link', `${obj.source} → ${obj.target}`]);
      items.push(['Shared by destinations', obj.sharedCount]);
      items.push(['TTL transition', `${obj.srcTTL} → ${obj.dstTTL}`]);
      items.push(['Average RTT', fmtMs(obj.avgRtt)]);
      items.push(['Loss', `${(obj.lossRate * 100).toFixed(1)}%`]);
    }
    detailList.innerHTML = items.map(([k, v]) => `<div class="info-item"><div class="label">${escapeHtml(k)}</div><div class="value">${escapeHtml(v)}</div></div>`).join('');
  }

  function renderRawRows(rows) {
    currentRawRows = rows.slice().sort((a, b) => a.target.localeCompare(b.target) || a.ttl - b.ttl || String(a.protocol).localeCompare(String(b.protocol)));
    currentPage = 0;
    repaintRawTable();
  }

  function repaintRawTable() {
    const totalPages = Math.max(1, Math.ceil(currentRawRows.length / PAGE_SIZE));
    currentPage = clamp(currentPage, 0, totalPages - 1);
    const start = currentPage * PAGE_SIZE;
    const pageRows = currentRawRows.slice(start, start + PAGE_SIZE);
    rawTable.innerHTML = pageRows.map(r => `
      <tr>
        <td>${escapeHtml(r.target)}</td>
        <td>${r.ttl}</td>
        <td>${r.series ?? 1}</td>
        <td>${escapeHtml(r.protocol)}</td>
        <td>${escapeHtml(r.success ? (r.reply_ip || '*') : '*')}</td>
        <td>${r.success && r.rtt_ms != null ? Number(r.rtt_ms).toFixed(1) : '–'}</td>
        <td>${r.success ? 'Yes' : 'No'}</td>
      </tr>`).join('');
    pageInfo.textContent = `Page ${currentPage + 1} / ${totalPages}`;
    prevPage.disabled = currentPage === 0;
    nextPage.disabled = currentPage >= totalPages - 1;
  }

  function createSvg(tag, attrs) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    return el;
  }

  function renderTree(agg) {
    clear(svg);
    const width = svg.clientWidth || 900;
    const height = svg.clientHeight || 600;
    const destinations = filteredDestinations();
    const aggregateDense = aggregateToggle.checked && destinations.length > 20;
    const sourceId = '__SOURCE__';
    const sourceNode = { id: sourceId, ip: typeof data.source === 'string' ? data.source : (data.source.ip || 'source'), ttlMin: 0, ttlMax: 0, sharedCount: destinations.length, avgRtt: 0, lossRate: 0, unknown: false };

    const nodesByTtl = new Map();
    for (const n of agg.nodes) {
      const ttl = n.ttlMin;
      if (!nodesByTtl.has(ttl)) nodesByTtl.set(ttl, []);
      nodesByTtl.get(ttl).push(n);
    }

    const keptSet = new Set();
    const displayNodes = [sourceNode];
    const ttlLevels = [...nodesByTtl.keys()].sort((a, b) => a - b);
    const perTtlCap = aggregateDense ? 6 : 1000;

    for (const ttl of ttlLevels) {
      const arr = (nodesByTtl.get(ttl) || []).slice().sort((a, b) => b.sharedCount - a.sharedCount || a.avgRtt - b.avgRtt);
      const kept = arr.slice(0, perTtlCap);
      kept.forEach(n => keptSet.add(n.id));
      displayNodes.push(...kept);
      if (aggregateDense && arr.length > kept.length) {
        const rest = arr.slice(kept.length);
        displayNodes.push({
          id: `OTHER@TTL${ttl}`,
          ip: `Other hops (${rest.length})`,
          ttlMin: ttl,
          ttlMax: ttl,
          sharedCount: Math.max(...rest.map(r => r.sharedCount)),
          avgRtt: mean(rest.map(r => r.avgRtt)),
          lossRate: mean(rest.map(r => r.lossRate)),
          unknown: false,
          aggregate: true,
          memberCount: rest.length
        });
      }
    }

    const displayNodeMap = new Map(displayNodes.map(n => [n.id, n]));
    const groupedEdges = new Map();

    for (const e of agg.edges) {
      const srcNode = agg.nodes.find(n => n.id === e.source);
      const dstNode = agg.nodes.find(n => n.id === e.target);
      if (!srcNode || !dstNode) continue;
      const gs = groupKeyForNode(srcNode, srcNode.ttlMin, keptSet);
      const gt = groupKeyForNode(dstNode, dstNode.ttlMin, keptSet);
      if (gs === gt) continue;
      const key = `${gs}=>${gt}`;
      if (!groupedEdges.has(key)) groupedEdges.set(key, { source: gs, target: gt, rtts: [], loss: [], sharedCount: 0, srcTTL: srcNode.ttlMin, dstTTL: dstNode.ttlMin });
      const ge = groupedEdges.get(key);
      ge.rtts.push(e.avgRtt);
      ge.loss.push(e.lossRate);
      ge.sharedCount += e.sharedCount;
    }

    for (const ttl1Nodes of agg.pathSummaries[0]?.path || []) { /* keep parser happy */ }

    // Connect source to first TTL groups.
    const firstTtl = ttlLevels[0];
    if (firstTtl != null) {
      const firstIds = displayNodes.filter(n => n.ttlMin === firstTtl).map(n => n.id);
      for (const id of firstIds) {
        const key = `${sourceId}=>${id}`;
        if (!groupedEdges.has(key)) groupedEdges.set(key, { source: sourceId, target: id, rtts: [], loss: [], sharedCount: destinations.length, srcTTL: 0, dstTTL: firstTtl });
      }
    }

    const displayEdges = [...groupedEdges.values()].map(e => ({
      ...e,
      avgRtt: mean(e.rtts),
      lossRate: mean(e.loss)
    })).filter(e => displayNodeMap.has(e.source) && displayNodeMap.has(e.target));

    const xForTtl = ttl => 80 + (ttl / Math.max(1, ttlLevels[ttlLevels.length - 1] || 1)) * (width - 160);
    const positions = new Map();
    positions.set(sourceId, { x: 60, y: height / 2 });
    for (const ttl of ttlLevels) {
      const col = displayNodes.filter(n => n.ttlMin === ttl).sort((a, b) => Number(!!a.aggregate) - Number(!!b.aggregate) || b.sharedCount - a.sharedCount);
      const gap = height / (col.length + 1);
      col.forEach((n, i) => positions.set(n.id, { x: xForTtl(ttl), y: gap * (i + 1) }));
    }

    const edgeLayer = createSvg('g');
    const labelLayer = createSvg('g');
    const nodeLayer = createSvg('g');
    svg.append(edgeLayer, labelLayer, nodeLayer);

    const ttlHeader = createSvg('g');
    for (const ttl of ttlLevels) {
      const x = xForTtl(ttl);
      ttlHeader.appendChild(createSvg('text', { x, y: 24, fill: '#a9b4d0', 'font-size': '12', 'text-anchor': 'middle' }));
      ttlHeader.lastChild.textContent = `TTL ${ttl}`;
      ttlHeader.appendChild(createSvg('line', { x1: x, y1: 36, x2: x, y2: height, stroke: '#22355d', 'stroke-dasharray': '4 6' }));
    }
    svg.appendChild(ttlHeader);

    const edgeMax = Math.max(...displayEdges.map(e => e.sharedCount), 1);
    for (const e of displayEdges) {
      const a = positions.get(e.source);
      const b = positions.get(e.target);
      if (!a || !b) continue;
      const line = createSvg('line', {
        x1: a.x, y1: a.y, x2: b.x, y2: b.y,
        stroke: protocolColor(protocolSelect.value),
        'stroke-opacity': aggregateDense ? 0.28 : 0.45,
        'stroke-width': 1.5 + (e.sharedCount / edgeMax) * 5
      });
      line.addEventListener('click', () => {
        selectedInfo = { type: 'edge', ...e };
        updateDetails(selectedInfo);
      });
      edgeLayer.appendChild(line);
      if (!aggregateDense) {
        const lx = (a.x + b.x) / 2, ly = (a.y + b.y) / 2;
        const label = createSvg('text', { x: lx, y: ly - 4, fill: '#d9dfea', 'font-size': '10', 'text-anchor': 'middle' });
        label.textContent = `${fmtMs(e.avgRtt)} | ${(e.lossRate * 100).toFixed(0)}%`;
        labelLayer.appendChild(label);
      }
    }

    const nodeMax = Math.max(...displayNodes.map(n => n.sharedCount || 1), 1);
    for (const n of displayNodes) {
      const p = positions.get(n.id);
      if (!p) continue;
      const r = n.id === sourceId ? 16 : 8 + (n.sharedCount / nodeMax) * (aggregateDense ? 14 : 12);
      const circle = createSvg('circle', {
        cx: p.x, cy: p.y, r,
        fill: n.id === sourceId ? '#ffd36b' : (n.aggregate ? '#9bb8f0' : (n.unknown ? '#7d859a' : '#8fb6ff')),
        stroke: '#e9edf8', 'stroke-width': 1.5
      });
      circle.addEventListener('click', () => {
        selectedInfo = { type: 'node', ...n };
        updateDetails(selectedInfo);
      });
      nodeLayer.appendChild(circle);
      const label = createSvg('text', { x: p.x, y: p.y + r + 14, fill: '#eef2ff', 'font-size': aggregateDense ? '11' : '12', 'text-anchor': 'middle' });
      label.textContent = n.id === sourceId ? 'source' : n.ip;
      nodeLayer.appendChild(label);
      const sub = createSvg('text', { x: p.x, y: p.y + r + 27, fill: '#a9b4d0', 'font-size': '10', 'text-anchor': 'middle' });
      sub.textContent = n.id === sourceId ? (typeof data.source === 'string' ? data.source : (data.source.ip || '')) : `${fmtMs(n.avgRtt)} | ${(n.lossRate * 100).toFixed(0)}% | ${n.sharedCount}`;
      nodeLayer.appendChild(sub);
    }

    vizOverlay.style.display = 'block';
    vizOverlay.textContent = aggregateDense
      ? 'Showing a shared-path summary. For each TTL, the plot keeps the most shared hops and compresses the rest into an “Other hops” bucket.'
      : 'Showing the direct path view for the selected destination(s). Click a node or link to inspect its details.';
  }

  function drawWorldBackground(width, height) {
    const bg = createSvg('g');
    bg.appendChild(createSvg('rect', { x: 0, y: 0, width, height, fill: '#091533' }));
    for (let lon = -150; lon <= 150; lon += 30) {
      const x = ((lon + 180) / 360) * width;
      bg.appendChild(createSvg('line', { x1: x, y1: 0, x2: x, y2: height, stroke: '#22355d' }));
    }
    for (let lat = -60; lat <= 60; lat += 30) {
      const y = ((90 - lat) / 180) * height;
      bg.appendChild(createSvg('line', { x1: 0, y1: y, x2: width, y2: y, stroke: '#22355d' }));
    }
    const continents = [
      'M 70 110 L 140 80 L 180 90 L 210 120 L 210 170 L 190 220 L 150 250 L 120 245 L 105 210 L 90 180 Z',
      'M 180 255 L 210 285 L 225 340 L 205 410 L 180 470 L 150 440 L 160 360 Z',
      'M 340 110 L 380 95 L 430 110 L 470 135 L 505 150 L 535 180 L 565 170 L 610 195 L 635 235 L 620 255 L 590 250 L 565 220 L 530 215 L 500 220 L 465 245 L 445 285 L 415 305 L 390 285 L 380 245 L 360 225 L 325 205 L 310 175 Z',
      'M 405 320 L 440 335 L 455 365 L 445 415 L 420 455 L 390 425 L 380 360 Z',
      'M 610 340 L 700 335 L 760 360 L 770 400 L 735 430 L 650 430 L 610 390 Z'
    ];
    continents.forEach(d => bg.appendChild(createSvg('path', { d, fill: '#172747', stroke: '#27436f', 'stroke-width': 1.2, opacity: 0.95 })));
    return bg;
  }

  function project(lat, lng, width, height) {
    return { x: ((lng + 180) / 360) * width, y: ((90 - lat) / 180) * height };
  }

  function renderMap(agg) {
    clear(svg);
    const width = svg.clientWidth || 900;
    const height = svg.clientHeight || 600;
    svg.appendChild(drawWorldBackground(width, height));

    const geonodes = agg.nodes.filter(n => Number.isFinite(n.lat) && Number.isFinite(n.lng) && !n.unknown);
    if (!geonodes.length) {
      vizOverlay.style.display = 'block';
      vizOverlay.textContent = 'No latitude/longitude fields were found in the input JSON, so the world map cannot place hops yet. The map view is ready, but it needs geolocation-enriched input.';
      return;
    }

    vizOverlay.style.display = 'block';
    vizOverlay.textContent = 'Showing aggregated geographic bins so the map stays readable. Each circle represents one geographic cell containing one or more hops.';

    const cellMap = new Map();
    const cellSizeLat = 18;
    const cellSizeLng = 36;
    for (const n of geonodes) {
      const latBin = Math.floor((n.lat + 90) / cellSizeLat);
      const lngBin = Math.floor((n.lng + 180) / cellSizeLng);
      const key = `${latBin}:${lngBin}`;
      if (!cellMap.has(key)) cellMap.set(key, { key, nodes: [], lat: 0, lng: 0, sharedCount: 0, avgRtt: [], ids: new Set() });
      const c = cellMap.get(key);
      c.nodes.push(n); c.lat += n.lat; c.lng += n.lng; c.sharedCount += n.sharedCount; c.avgRtt.push(n.avgRtt); c.ids.add(n.id);
    }
    const cells = [...cellMap.values()].map(c => ({
      ...c,
      lat: c.lat / c.nodes.length,
      lng: c.lng / c.nodes.length,
      avgRtt: mean(c.avgRtt),
      size: c.nodes.length
    }));
    const cellIndex = new Map(cells.map(c => [...c.ids].map(id => [id, c.key])).flat());

    const edgeBins = new Map();
    for (const e of agg.edges) {
      const a = cellIndex.get(e.source), b = cellIndex.get(e.target);
      if (!a || !b || a === b) continue;
      const key = `${a}=>${b}`;
      if (!edgeBins.has(key)) edgeBins.set(key, { source: a, target: b, sharedCount: 0, rtts: [] });
      const eb = edgeBins.get(key); eb.sharedCount += e.sharedCount; eb.rtts.push(e.avgRtt);
    }

    const edgeLayer = createSvg('g');
    const nodeLayer = createSvg('g');
    svg.append(edgeLayer, nodeLayer);
    const edgeMax = Math.max(...[...edgeBins.values()].map(e => e.sharedCount), 1);
    for (const e of edgeBins.values()) {
      const a = cells.find(c => c.key === e.source), b = cells.find(c => c.key === e.target);
      if (!a || !b) continue;
      const p1 = project(a.lat, a.lng, width, height);
      const p2 = project(b.lat, b.lng, width, height);
      edgeLayer.appendChild(createSvg('line', {
        x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y,
        stroke: protocolColor(protocolSelect.value),
        'stroke-opacity': 0.25,
        'stroke-width': 1 + (e.sharedCount / edgeMax) * 4
      }));
    }
    const nodeMax = Math.max(...cells.map(c => c.sharedCount), 1);
    for (const c of cells) {
      const p = project(c.lat, c.lng, width, height);
      const r = 5 + (c.sharedCount / nodeMax) * 18;
      const circle = createSvg('circle', { cx: p.x, cy: p.y, r, fill: '#8fb6ff', stroke: '#eef2ff', 'stroke-width': 1.2, opacity: 0.9 });
      circle.addEventListener('click', () => {
        selectedInfo = {
          type: 'node', ip: `${c.size} hops in cell`, ttlMin: '–', ttlMax: '–', sharedCount: c.sharedCount,
          avgRtt: c.avgRtt, lossRate: 0, reached: false
        };
        updateDetails(selectedInfo);
      });
      nodeLayer.appendChild(circle);
      const label = createSvg('text', { x: p.x, y: p.y - r - 4, fill: '#dce6ff', 'font-size': '10', 'text-anchor': 'middle' });
      label.textContent = `${c.size} hops`;
      nodeLayer.appendChild(label);
    }
  }

  function rerender() {
    const agg = aggregate();
    const metrics = computeMetrics(agg);
    renderCards(metrics);
    renderBars(rttChart, 'Average RTT by protocol', metrics.avgRttByProto, v => `${v.toFixed(1)} ms`);
    renderBars(lossChart, 'Loss rate by protocol', metrics.lossByProto, v => `${(v * 100).toFixed(1)}%`, 1);
    renderProtocolTable(metrics);
    renderSharedTable(agg);
    renderRawRows(agg.rawRows);
    updateDetails(selectedInfo);
    viewTitle.textContent = viewSelect.value === 'map' ? 'Map View' : 'Tree View';
    if (viewSelect.value === 'map') renderMap(agg); else renderTree(agg);
  }

  prevPage.addEventListener('click', () => { currentPage--; repaintRawTable(); });
  nextPage.addEventListener('click', () => { currentPage++; repaintRawTable(); });
  [viewSelect, protocolSelect, destinationSelect, unknownToggle, aggregateToggle].forEach(el => el.addEventListener('change', rerender));
  searchInput.addEventListener('input', applyDestinationSearch);
  buildDestinationOptions();
  rerender();
})();
