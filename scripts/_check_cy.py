"""Check vendored libs integrity."""
with open(r"web\static\vendor\cytoscape.min.js", "rb") as f:
    data = f.read()
print("cytoscape: size=", len(data), "starts=", data[:50], "has_cytoscape=", b"cytoscape" in data)

with open(r"web\static\vendor\markdown-it.min.js", "rb") as f:
    data = f.read()
print("markdown-it: size=", len(data), "starts=", data[:50], "has_mdit=", b"markdownit" in data)