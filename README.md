# Internet Topology Explorer

## Overview

This project implements:

* A **custom traceroute-like analyzer** supporting UDP, TCP, and ICMP probes
* A **geographic topology visualizer** for exploring network paths

The system collects traceroute data, enriches it with geolocation and ASN information, and displays it interactively in a browser.

---

## Source Structure

```id="tree123"
.
├── topology_visualizer/
│   ├── app.js
│   ├── data.js
│   ├── enrich_geolocation.py
│   ├── enriched_results.json
│   ├── index.html
│   ├── patch_source.py
│   ├── README.md
│   ├── run_visualizer.py
│   ├── sample_data.json
│   └── style.css
│   
├── .gitignore
├── destination_prefixes.txt
├── HOWTO.md
├── Makefile
├── mini_traceroute.py
├── README.md
└── run_analyzer
```

---

## Core Components

### Analyzer

- **`mini_traceroute.py`**  
  Custom traceroute implementation using UDP, TCP, and ICMP probes. Outputs structured JSON. Requires root privileges.

- **`run_analyzer`**  
  Main executable pipeline. Runs traceroute, downloads GeoLite2 databases, enriches results with geolocation/ASN, and prepares output for visualization.

- **`destination_prefixes.txt`**  
  Input file containing target IPs or prefixes.

---

### Visualization (`topology_visualizer/`)

- **`run_visualizer.py`**  
  Starts a local server and launches the visualization.

- **Frontend (`index.html`, `app.js`, `style.css`)**  
  Interactive map and analysis UI (Leaflet + charts).

- **`data.js`**  
  Auto-generated data used by the frontend.

- **`enrich_geolocation.py`**  
  Adds geolocation and ASN data using MaxMind databases.

- **`patch_source.py`**  
  Fixes the source node location for visualization.

- **`sample_data.json` / `enriched_results.json`**  
  Raw and processed traceroute outputs.

---

### Build & Execution

- **`Makefile`**  
  Common commands: `make install`, `make all`, `make clean`.

- **`HOWTO.md`**  
  Detailed setup and usage instructions.

- **`README.md`**  
  Project overview and structure.

---

## Notes

- Private IPs are not geolocated.  
- Visualization uses the enriched JSON output.