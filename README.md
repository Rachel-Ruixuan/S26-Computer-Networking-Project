# Internet Topology Explorer
## 1. Environment setup
```bash
conda create -n network python=3.9
pip install networkx matplotlib pygraphviz plotly
```

## 2. Kathara
**1) setup**
```bash
kathara lstart
```

**2) on pc2 & pc3:**
type `nc -nkl 33434`

**3) on pc1:**
1. `cat > mini_traceroute.py` 
2. copy all content from `mini_traceroute.py` 
3. paste the copied content and  `Ctrl + D` (if nothing happens then `Ctrl + D` again)
4. Similarly, `cat > targets.csv` then paste all content from `targets.csv` and `Ctrl + D`
5. `python3 mini_traceroute.py --input targets.csv --max-ttl 5 --port 33434 --num-series 2`

## 3. Visualization
**1) copy result**
1. `cat > results.json` and copy all content
2. paste as save as `results.json` under the project directory

**2) plot:**
under the project directory, run
```bash
conda activate network
python3 visualize_plotly.py --input results.json --output topology_interactive.html
```
This will generate `topology_interactive.html`. To visualize this interactive html, install the `Live Server` extension from VSCode extensions, open `topology_interactive.html` and click on "Go Live" to redirect to the browser.

---
## Todo
1. check whether the **analyzer** means the mini_traceroute.py, checking the rtt, and the visualizer, or that we need a very specific analyzer class so that we can have a "a b**inary file** or a **makefile** to run the execution of your analyzer"
2. check whether the Kathara setup is valid because the requirement says that input needs to be **csv/txt** but Kathara is the basic setup (not sure whether this is allowed/appropriate)
3. **visualization:** better styling, more visible link **length** & **thickness** difference
4. Link hover double check
5. (first check w/ professor abt data input and then) check whether we need to create more input cases to make full use of the visualizer (different color of links)
