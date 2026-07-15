# Lookup File Formats and Limits

## CSV Format

Lookup files are most commonly uploaded as CSV (comma-separated values).

### Requirements

- **Header row required.** The first row must contain column names.
- **Comma-delimited.** Use commas as the field separator.
- **UTF-8 encoding.** Files must be encoded in UTF-8.
- **No BOM.** Do not include a byte order mark.
- **Consistent columns.** Every row must have the same number of fields.

### Example

```csv
ip,category,source,added_date
10.0.0.1,c2,threat-intel,2026-01-15
192.168.1.100,scanner,internal-scan,2026-02-01
172.16.0.50,malware,external-feed,2026-03-10
```

### Quoting Rules

- Fields containing commas must be enclosed in double quotes: `"New York, NY"`
- Fields containing double quotes must escape them: `"He said ""hello"""`
- Fields containing newlines must be quoted

## JSON Format

Lookup files can also be uploaded as JSON.

### Requirements

- **Array of objects.** The top-level structure must be a JSON array.
- **Consistent schema.** Each object should have the same keys.
- **UTF-8 encoding.**

### Example

```json
[
  {"ip": "10.0.0.1", "category": "c2", "source": "threat-intel"},
  {"ip": "192.168.1.100", "category": "scanner", "source": "internal-scan"}
]
```

## Operational Limits

| Limit | Value |
|-------|-------|
| Upload rate | 5 files per 30 seconds |
| Recommended max file size | 10 MB |
| File name characters | Alphanumeric, hyphens, underscores, dots |

## Search Domains

`create_lookup.py` and `update_lookup.py` upload to the global namespace with no
search domain, so CQL `match()` can resolve the file by name. This is what you
want for almost every lookup. Scoping a file to a search view hides it from
`match()` in a normal search.

Read operations (`list_lookups.py`, `get_lookup.py`, `delete_lookup.py`) accept
an optional `--domain` filter:

| Domain | Description |
|--------|-------------|
| `all` | Search across all domains (default for read operations) |
| `falcon` | Files scoped to the Falcon Next-Gen SIEM view |
| `third-party` | Files from third-party integrations |
| `parsers-repository` | Files used by log parsers |
| `dashboards` | Dashboard-specific files (read-only) |

**Recommendation:** Upload without a search domain (the default) so `match()`
can find the file.

## Naming Conventions

- Use lowercase with hyphens: `ip-blocklist.csv`, `user-risk-scores.csv`
- Include the file extension: `.csv` or `.json`
- Be descriptive: the filename is how CQL queries reference the file

## Built-in Playbook Reference

CrowdStrike provides a built-in Fusion playbook called **"Introduction to
Lookup file actions"** that demonstrates the full create/overwrite flow. Access
it via: Create workflow > Workflow Playbooks in the Falcon console.

This playbook shows:
1. Checking if a lookup file exists (Get lookup file metadata)
2. Creating a new file if it does not exist
3. Overwriting an existing file with updated content
