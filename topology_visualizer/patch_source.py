import json
import sys

if len(sys.argv) != 2:
    print("Usage: python patch_source.py <json_file>")
    sys.exit(1)

json_file = sys.argv[1]

with open(json_file, "r", encoding="utf-8") as f:
    data = json.load(f)

data["source"] = {
    "ip": "10.209.84.68",
    "lat": 31.2304,
    "lng": 121.4737,
    "city": "Shanghai",
    "region": "Shanghai",
    "country": "China"
}

with open(json_file, "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2)

print(f"Updated source field in {json_file}")