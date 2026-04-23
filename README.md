# Internet Topology Explorer
## 1. Environment setup
```bash
conda create -n network python=3.9
pip install networkx matplotlib geoip2 pandas
```

## 2. Traceroute
under the project main directory, run 
```bash
sudo python3 mini_traceroute.py \
  --input destination_prefixes.txt \
  --max-ttl 20 \
  --timeout 1.0 \
  -n \
  --output topology_visualizer/sample_data.json
```

## 3. Visualization
**1) add ip location**
```bash
conda activate network
cd topology_visualizer
wget https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-ASN.mmdb # to download GeoLite2-ASN.mmdb
wget https://github.com/P3TERX/GeoLite.mmdb/raw/download/GeoLite2-City.mmdb # to download GeoLite2-City.mmdb

python enrich_geolocation.py sample_data.json enriched_results.json \
  --mmdb GeoLite2-City.mmdb \
  --asn-mmdb GeoLite2-ASN.mmdb
```
then open `enriched_results.json` to replace the first line "source" with 
```
"source": {
    "ip": "10.209.84.68",
    "lat": 31.2304,
    "lng": 121.4737,
    "city": "Shanghai",
    "region": "Shanghai",
    "country": "China"
  },
```

**2) plot:**
```bash
python3 run_visualizer.py enriched_results.json # to view localhost visualization
```

---
## Todo
1. "a **binary file** or a **makefile** to run the execution of the analyzer"
2. Link hover show destination?
