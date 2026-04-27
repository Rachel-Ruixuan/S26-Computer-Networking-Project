# HOW TO INSTALL AND RUN

## Reuse Existing Probing Data

The repository already includes `topology_visualizer/enriched_results.json`, so you can skip the traceroute probing step.

```bash
make install
make visualize
```

This directly launches the visualizer using the existing enriched data.


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