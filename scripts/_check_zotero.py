"""Check why ZoteroClient picks SQLite."""
from sciassist.utils.zotero_client import ZoteroClient, HTTPBackend, SQLiteBackend, BBTBackend

print("HTTPBackend ping:", HTTPBackend().ping())
print("SQLiteBackend ping:", SQLiteBackend().ping())
print("BBTBackend ping:", BBTBackend().ping())

z = ZoteroClient()
print()
print("chosen backend:", type(z.backend).__name__)
print("health:", z.health_check())