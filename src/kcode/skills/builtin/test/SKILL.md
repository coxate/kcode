---
name: test
description: Select and run focused tests, then explain any failures.
allowed_tools:
  - read_file
  - search_code
  - find_files
  - run_command
mode: inline
---
Identify the smallest relevant test set, run it, and report the observed results. Diagnose failures without hiding pre-existing problems or changing unrelated files.

$ARGUMENTS
