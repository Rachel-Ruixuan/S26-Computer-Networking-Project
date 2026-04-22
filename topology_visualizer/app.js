(function () {
  const data = window.TRACE_DATA || { source: "unknown", destinations: [] };

  const COLORS = {
    NODE: "#63b3ff",
    SOURCE: "#ffd36b",
    DESTINATION: "#ff8f6b",
    BASE_PATH: "#63b3ff",
    SELECTED_NODE: "#ffffff"
  };

  const MULTI_PATH_COLORS = [
    "#ff6b6b",
    "#4dabf7",
    "#51cf66",
    "#fcc419",
    "#b197fc",
    "#ff922b",
    "#20c997",
    "#f06595",
    "#94d82d",
    "#74c0fc"
  ];

  const CHART_COLORS = {
    bar: "rgba(99, 179, 255, 0.78)",
    border: "#63b3ff",
    grid: "rgba(169, 180, 208, 0.14)",
    ticks: "#c9d3eb",
    title: "#e8ecf8",
    tooltipBg: "rgba(12, 18, 36, 0.96)"
  };

  let map;
  let markerLayer;
  let basePathLayer;
  let sourceLayer;
  let selectedNodeLayer;
  let activeHighlightLayer = null;

  let pathLengthChart = null;
  let rttJumpChart = null;
  let asnTransitionChart = null;

  function mean(nums) {
    return nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : 0;
  }

  function fmtMs(v) {
    return Number.isFinite(v) ? `${v.toFixed(1)} ms` : "–";
  }

  function escapeHtml(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  function getSourceIp() {
    if (typeof data.source === "string") return data.source;
    return data.source?.ip || "unknown";
  }

  function getSourceLocationLabel() {
    if (typeof data.source === "string") return "–";
    return [data.source?.city, data.source?.region, data.source?.country]
      .filter(Boolean)
      .join(", ") || "–";
  }

  function getSourceLatLng() {
    if (typeof data.source === "string") return null;
    if (!Number.isFinite(data.source?.lat) || !Number.isFinite(data.source?.lng)) return null;
    return [Number(data.source.lat), Number(data.source.lng)];
  }

  function initMap() {
    map = L.map("map", {
      worldCopyJump: true,
      preferCanvas: true
    }).setView([20, 0], 2);

    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: "&copy; OpenStreetMap contributors"
    }).addTo(map);

    basePathLayer = L.layerGroup().addTo(map);
    sourceLayer = L.layerGroup().addTo(map);
    selectedNodeLayer = L.layerGroup().addTo(map);

    markerLayer = L.markerClusterGroup({
      showCoverageOnHover: false,
      spiderfyOnMaxZoom: true,
      zoomToBoundsOnClick: true,
      chunkedLoading: true
    });

    map.addLayer(markerLayer);
  }

  function buildPathForDestination(dst) {
    const ttlMap = new Map();

    for (const p of dst.probes || []) {
      if (!p.success || !p.reply_ip) continue;
      if (!Number.isFinite(p.lat) || !Number.isFinite(p.lng)) continue;

      const ttl = Number(p.ttl);
      if (!ttlMap.has(ttl)) ttlMap.set(ttl, []);
      ttlMap.get(ttl).push(p);
    }

    const orderedTtls = [...ttlMap.keys()].sort((a, b) => a - b);
    const path = [];

    for (const ttl of orderedTtls) {
      const probes = ttlMap.get(ttl);
      const chosen = probes[0];

      path.push({
        destination: dst.target,
        ttl,
        ip: chosen.reply_ip,
        hostname: chosen.reply_name || "",
        city: chosen.city || "",
        country: chosen.country || "",
        region: chosen.region || "",
        lat: Number(chosen.lat),
        lng: Number(chosen.lng),
        rtt_ms: chosen.rtt_ms != null ? Number(chosen.rtt_ms) : null,
        reached_destination: !!chosen.reached_destination,
        asn: chosen.asn ?? null,
        asn_org: chosen.asn_org || ""
      });
    }

    return path;
  }

  function buildModel(destinations) {
    const pathsByDestination = new Map();
    const ipNodeMap = new Map();
    const targetIps = new Set(destinations.map(d => d.target));
    const sourceIp = getSourceIp();

    for (const dst of destinations) {
      const path = buildPathForDestination(dst);
      if (!path.length) continue;

      pathsByDestination.set(dst.target, path);

      for (const node of path) {
        const key = node.ip;

        if (!ipNodeMap.has(key)) {
          ipNodeMap.set(key, {
            ip: node.ip,
            lat: node.lat,
            lng: node.lng,
            city: node.city,
            country: node.country,
            region: node.region,
            asn: node.asn,
            asn_org: node.asn_org,
            occurrences: []
          });
        }

        const entry = ipNodeMap.get(key);
        entry.occurrences.push(node);

        if (!entry.city && node.city) entry.city = node.city;
        if (!entry.country && node.country) entry.country = node.country;
        if (!entry.region && node.region) entry.region = node.region;
        if (!entry.asn && node.asn) entry.asn = node.asn;
        if (!entry.asn_org && node.asn_org) entry.asn_org = node.asn_org;
      }
    }

    const ipNodes = [...ipNodeMap.values()].map(node => {
      const destinationsSet = new Set(node.occurrences.map(x => x.destination));
      const ttlValues = node.occurrences.map(x => x.ttl);
      const rtts = node.occurrences
        .map(x => x.rtt_ms)
        .filter(v => Number.isFinite(v));

      let role = "router";
      if (node.ip === sourceIp) {
        role = "source";
      } else if (targetIps.has(node.ip)) {
        role = "destination";
      }

      return {
        ...node,
        role,
        destinations: [...destinationsSet],
        ttlMin: Math.min(...ttlValues),
        ttlMax: Math.max(...ttlValues),
        avgRtt: mean(rtts),
        occurrenceCount: node.occurrences.length
      };
    });

    return { pathsByDestination, ipNodes, targetIps, sourceIp };
  }

  function clearLayers() {
    markerLayer.clearLayers();
    basePathLayer.clearLayers();
    sourceLayer.clearLayers();
    selectedNodeLayer.clearLayers();

    if (activeHighlightLayer) {
      map.removeLayer(activeHighlightLayer);
      activeHighlightLayer = null;
    }
  }

  function popupHtml(ipNode) {
    const location = [ipNode.city, ipNode.region, ipNode.country]
      .filter(Boolean)
      .join(", ") || "–";
    const asnText = ipNode.asn
      ? `${ipNode.asn}${ipNode.asn_org ? ` (${ipNode.asn_org})` : ""}`
      : "–";

    return `
      <div class="popup-title">${escapeHtml(ipNode.ip)}</div>
      <div class="popup-grid">
        <div class="popup-label">Role</div>
        <div>${escapeHtml(ipNode.role)}</div>

        <div class="popup-label">Location</div>
        <div>${escapeHtml(location)}</div>

        <div class="popup-label">Avg RTT</div>
        <div>${escapeHtml(fmtMs(ipNode.avgRtt))}</div>

        <div class="popup-label">TTL range</div>
        <div>${escapeHtml(`${ipNode.ttlMin} – ${ipNode.ttlMax}`)}</div>

        <div class="popup-label">Destination count</div>
        <div>${escapeHtml(String(ipNode.destinations.length))}</div>

        <div class="popup-label">ASN</div>
        <div>${escapeHtml(asnText)}</div>
      </div>
    `;
  }

  function sourcePopupHtml() {
    return `
      <div class="popup-title">${escapeHtml(getSourceIp())}</div>
      <div class="popup-grid">
        <div class="popup-label">Role</div>
        <div>source</div>

        <div class="popup-label">Location</div>
        <div>${escapeHtml(getSourceLocationLabel())}</div>

        <div class="popup-label">Avg RTT</div>
        <div>–</div>

        <div class="popup-label">TTL range</div>
        <div>0</div>

        <div class="popup-label">Destination count</div>
        <div>${escapeHtml(String((data.destinations || []).length))}</div>

        <div class="popup-label">ASN</div>
        <div>–</div>
      </div>
    `;
  }

  function drawSourceMarker() {
    const sourceLatLng = getSourceLatLng();
    if (!sourceLatLng) return;

    const marker = L.circleMarker(sourceLatLng, {
      radius: 9,
      color: "#ffffff",
      weight: 1.6,
      fillColor: COLORS.SOURCE,
      fillOpacity: 0.96
    });

    marker.bindPopup(sourcePopupHtml());

    marker.bindTooltip("Source", {
      permanent: true,
      direction: "top",
      offset: [0, -10]
    });

    marker.on("mouseover", function () {
      this.openPopup();
    });

    sourceLayer.addLayer(marker);
  }

  function drawSelectedNodeHighlight(ipNode) {
    selectedNodeLayer.clearLayers();

    const marker = L.circleMarker([ipNode.lat, ipNode.lng], {
      radius: Math.min(16, 8 + Math.log2(ipNode.occurrenceCount + 1)),
      color: "#000000",
      weight: 2.2,
      fillColor: COLORS.SELECTED_NODE,
      fillOpacity: 1.0
    });

    selectedNodeLayer.addLayer(marker);
  }

  function drawBasePaths(pathsByDestination) {
    const bounds = [];
    const sourceLatLng = getSourceLatLng();

    for (const [target, path] of pathsByDestination.entries()) {
      if (!path.length) continue;

      const coords = path.map(n => [n.lat, n.lng]);

      if (coords.length >= 2) {
        const line = L.polyline(coords, {
          color: COLORS.BASE_PATH,
          weight: 1.4,
          opacity: 0.18
        }).addTo(basePathLayer);

        line.on("click", () => {
          highlightSinglePath(target, pathsByDestination);
        });
      }

      if (sourceLatLng && coords.length >= 1) {
        const sourceToFirst = L.polyline([sourceLatLng, coords[0]], {
          color: COLORS.BASE_PATH,
          weight: 1.0,
          opacity: 0.14,
          dashArray: "4 4"
        }).addTo(basePathLayer);

        sourceToFirst.on("click", () => {
          highlightSinglePath(target, pathsByDestination);
        });
      }

      if (sourceLatLng) bounds.push(sourceLatLng);
      coords.forEach(c => bounds.push(c));
    }

    if (bounds.length) {
      map.fitBounds(bounds, { padding: [30, 30] });
    }
  }

  function buildFullCoords(path) {
    const coords = path.map(n => [n.lat, n.lng]);
    const sourceLatLng = getSourceLatLng();
    if (sourceLatLng && coords.length) {
      return [sourceLatLng, ...coords];
    }
    return coords;
  }

  function highlightSinglePath(target, pathsByDestination) {
    if (activeHighlightLayer) {
      map.removeLayer(activeHighlightLayer);
      activeHighlightLayer = null;
    }

    const path = pathsByDestination.get(target);
    if (!path || !path.length) return;

    const coords = buildFullCoords(path);

    activeHighlightLayer = L.polyline(coords, {
      color: COLORS.SOURCE,
      weight: 5,
      opacity: 0.92
    }).addTo(map);

    activeHighlightLayer.bringToFront();
    map.fitBounds(activeHighlightLayer.getBounds(), { padding: [40, 40] });
  }

  function highlightAllPathsThroughIp(ip, model) {
    if (activeHighlightLayer) {
      map.removeLayer(activeHighlightLayer);
      activeHighlightLayer = null;
    }

    const layers = [];
    let colorIndex = 0;

    for (const [target, path] of model.pathsByDestination.entries()) {
      if (!path.some(n => n.ip === ip)) continue;

      const coords = buildFullCoords(path);
      const color = MULTI_PATH_COLORS[colorIndex % MULTI_PATH_COLORS.length];
      colorIndex += 1;

      const line = L.polyline(coords, {
        color,
        weight: 4.5,
        opacity: 0.9
      });

      line.bindTooltip(`Path to ${target}`, {
        sticky: true
      });

      layers.push(line);
    }

    if (!layers.length) return;

    activeHighlightLayer = L.layerGroup(layers).addTo(map);

    let bounds = null;
    for (const line of layers) {
      bounds = bounds ? bounds.extend(line.getBounds()) : line.getBounds();
    }
    if (bounds) {
      map.fitBounds(bounds, { padding: [40, 40] });
    }
  }

  function getMarkerStyle(ipNode) {
    if (ipNode.role === "destination") {
      return {
        radius: Math.min(12.5, 6 + Math.log2(ipNode.occurrenceCount + 1)),
        color: "#ffffff",
        weight: 1.4,
        fillColor: COLORS.DESTINATION,
        fillOpacity: 0.96
      };
    }

    return {
      radius: Math.min(11, 4.8 + Math.log2(ipNode.occurrenceCount + 1)),
      color: "#ffffff",
      weight: 1.2,
      fillColor: COLORS.NODE,
      fillOpacity: 0.92
    };
  }

  function drawIpMarkers(model) {
    for (const ipNode of model.ipNodes) {
      const marker = L.circleMarker([ipNode.lat, ipNode.lng], getMarkerStyle(ipNode));

      marker.bindPopup(popupHtml(ipNode));

      if (ipNode.role === "destination") {
        marker.bindTooltip(`Destination: ${ipNode.ip}`, {
          direction: "top",
          offset: [0, -8]
        });
      }

      marker.on("mouseover", function () {
        this.openPopup();
      });

      marker.on("click", function () {
        this.openPopup();
        drawSelectedNodeHighlight(ipNode);

        if (ipNode.role === "destination") {
          highlightSinglePath(ipNode.ip, model.pathsByDestination);
        } else {
          highlightAllPathsThroughIp(ipNode.ip, model);
        }
      });

      markerLayer.addLayer(marker);
    }
  }

  function countAsnTransitions(path) {
    let transitions = 0;
    let prev = null;

    for (const node of path) {
      const current = node.asn != null && node.asn !== "" ? String(node.asn) : null;
      if (current == null) continue;

      if (prev != null && current !== prev) {
        transitions += 1;
      }
      prev = current;
    }

    return transitions;
  }

  function largestPositiveRttJump(path) {
    let maxJump = 0;

    for (let i = 1; i < path.length; i += 1) {
      const prev = path[i - 1].rtt_ms;
      const curr = path[i].rtt_ms;

      if (!Number.isFinite(prev) || !Number.isFinite(curr)) continue;

      const jump = curr - prev;
      if (jump > maxJump) maxJump = jump;
    }

    return Math.round(maxJump);
  }

  function buildAnalysisMetrics(pathsByDestination) {
    const pathLengths = [];
    const rttJumps = [];
    const asnTransitions = [];

    for (const [, path] of pathsByDestination.entries()) {
      if (!path.length) continue;

      pathLengths.push(path.length);
      rttJumps.push(largestPositiveRttJump(path));
      asnTransitions.push(countAsnTransitions(path));
    }

    return {
      pathLengths,
      rttJumps,
      asnTransitions
    };
  }

  function buildCountMap(values) {
    const counts = new Map();
    for (const v of values) {
      const key = Number(v);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    return [...counts.entries()].sort((a, b) => a[0] - b[0]);
  }

  function buildHistogram(values, binSize) {
    if (!values.length) return [];

    const maxValue = Math.max(...values);
    const binCount = Math.max(1, Math.ceil((maxValue + 1) / binSize));
    const bins = Array.from({ length: binCount }, (_, i) => ({
      start: i * binSize,
      end: i * binSize + binSize - 1,
      count: 0
    }));

    for (const value of values) {
      const index = Math.min(Math.floor(value / binSize), bins.length - 1);
      bins[index].count += 1;
    }

    return bins;
  }

  function destroyCharts() {
    [pathLengthChart, rttJumpChart, asnTransitionChart].forEach(chart => {
      if (chart) chart.destroy();
    });

    pathLengthChart = null;
    rttJumpChart = null;
    asnTransitionChart = null;
  }

  function baseChartOptions(xTitle, yTitle) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: CHART_COLORS.tooltipBg,
          borderColor: "rgba(99, 179, 255, 0.35)",
          borderWidth: 1,
          titleColor: "#ffffff",
          bodyColor: "#e8ecf8",
          padding: 10
        }
      },
      scales: {
        x: {
          ticks: {
            color: CHART_COLORS.ticks
          },
          title: {
            display: true,
            text: xTitle,
            color: CHART_COLORS.ticks
          },
          grid: {
            color: CHART_COLORS.grid
          },
          border: {
            color: CHART_COLORS.grid
          }
        },
        y: {
          beginAtZero: true,
          ticks: {
            color: CHART_COLORS.ticks,
            precision: 0
          },
          title: {
            display: true,
            text: yTitle,
            color: CHART_COLORS.ticks
          },
          grid: {
            color: CHART_COLORS.grid
          },
          border: {
            color: CHART_COLORS.grid
          }
        }
      }
    };
  }

  function renderCharts(pathsByDestination) {
    destroyCharts();

    const metrics = buildAnalysisMetrics(pathsByDestination);

    const pathLengthCounts = buildCountMap(metrics.pathLengths);
    const asnTransitionCounts = buildCountMap(metrics.asnTransitions);
    const rttBins = buildHistogram(metrics.rttJumps, 100);

    const pathCtx = document.getElementById("pathLengthChart");
    const rttCtx = document.getElementById("rttJumpChart");
    const asnCtx = document.getElementById("asnTransitionChart");

    if (pathCtx) {
      pathLengthChart = new Chart(pathCtx, {
        type: "bar",
        data: {
          labels: pathLengthCounts.map(([k]) => String(k)),
          datasets: [{
            data: pathLengthCounts.map(([, v]) => v),
            backgroundColor: CHART_COLORS.bar,
            borderColor: CHART_COLORS.border,
            borderWidth: 1.2,
            borderRadius: 4
          }]
        },
        options: {
          ...baseChartOptions("Hop count per destination", "Number of destinations")
        }
      });
    }

    if (rttCtx) {
      rttJumpChart = new Chart(rttCtx, {
        type: "bar",
        data: {
          labels: rttBins.map(bin => `${bin.start}-${bin.end}`),
          datasets: [{
            data: rttBins.map(bin => bin.count),
            backgroundColor: CHART_COLORS.bar,
            borderColor: CHART_COLORS.border,
            borderWidth: 1.2,
            borderRadius: 4
          }]
        },
        options: {
          ...baseChartOptions("Largest positive RTT jump (ms)", "Number of destinations"),
          plugins: {
            ...baseChartOptions("", "").plugins,
            tooltip: {
              ...baseChartOptions("", "").plugins.tooltip,
              callbacks: {
                title(items) {
                  const i = items[0].dataIndex;
                  const bin = rttBins[i];
                  return `RTT jump: ${bin.start}-${bin.end} ms`;
                },
                label(context) {
                  return `Destinations: ${context.raw}`;
                }
              }
            }
          }
        }
      });
    }

    if (asnCtx) {
      asnTransitionChart = new Chart(asnCtx, {
        type: "bar",
        data: {
          labels: asnTransitionCounts.map(([k]) => String(k)),
          datasets: [{
            data: asnTransitionCounts.map(([, v]) => v),
            backgroundColor: CHART_COLORS.bar,
            borderColor: CHART_COLORS.border,
            borderWidth: 1.2,
            borderRadius: 4
          }]
        },
        options: {
          ...baseChartOptions("ASN transitions per destination", "Number of destinations")
        }
      });
    }
  }

  function render() {
    clearLayers();

    const selectedDestinations = data.destinations || [];
    const model = buildModel(selectedDestinations);

    drawBasePaths(model.pathsByDestination);
    drawSourceMarker();
    drawIpMarkers(model);
    renderCharts(model.pathsByDestination);
  }

  initMap();
  render();
})();