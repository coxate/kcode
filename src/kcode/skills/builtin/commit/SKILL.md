---
name: commit
description: Inspect the current changes and prepare a clear, safe commit.
allowed_tools:
  - read_file
  - search_code
  - find_files
  - run_command
mode: inline
---
Review the current repository changes, verify the relevant tests, and prepare a concise commit summary. Do not discard unrelated user changes. Do not commit unless the user explicitly requested it.

$ARGUMENTS
