# HOW TO INSTALL AND RUN

## Requirements

* Python **3.8+** (3.9 recommended)
* `sudo` access (required for raw socket operations in traceroute)
* `conda` (recommended) or `pip`

---

## Quick Start (Recommended: Conda + Makefile)

```bash id="conda1"
make install
make all
```

This will automatically:

* run traceroute probing
* generate `sample_data.json`
* download GeoLite2 databases (if missing)
* enrich results with geolocation + ASN
* produce `enriched_results.json`

The full pipeline is implemented in .