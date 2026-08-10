---
name: review
description: Review the current project for correctness, security, and test gaps.
allowed_tools:
  - read_file
  - search_code
  - find_files
  - run_command
mode: fork
fork_context: none
---
Review the current project. Prioritize correctness, security, concurrency risks, regressions, and missing tests. Report findings by severity with concrete file references. Do not modify files.

$ARGUMENTS
