# KCode Prompt Cache Smoke Guide

> These checks call real model APIs and may incur charges. Run them only with an intentionally
> selected test account and provider configuration. Never paste an API key into terminal output,
> screenshots, or this document.

## Preconditions

1. Install the development environment with `uv sync --extra dev`.
2. Configure exactly one test Provider through the normal KCode configuration and an environment
   variable reference for its key.
3. Use a model whose minimum cacheable prefix is no larger than KCode's stable prompt plus tool
   definitions. A zero result can mean that the prefix is below the model's threshold.
4. Keep the stable prompt, model and enabled tool set unchanged between the two requests.
5. Send the second request only after the first response has started, preferably after it completes.
6. Keep the second request inside the provider's cache lifetime. Anthropic uses the default
   five-minute TTL in this implementation.

## Observation method

Use a small, temporary local harness that imports the normal configuration loader and Provider
factory, calls `provider.stream(...)` twice, and prints only each `UsageReported.usage` value. The
harness must use `StableSystemMessage` plus different dynamic environment/user messages for the two
requests. Do not add this harness to the default test suite and do not print the loaded config.

Record only:

```text
provider/model:
request 1 cache creation:
request 1 cache read:
request 2 cache creation:
request 2 cache read:
elapsed time between requests:
```

## Expected provider fields

### Anthropic

- The first eligible request normally reports `cache_creation_input_tokens > 0`.
- A matching request within the TTL should report `cache_read_input_tokens > 0`.
- Both values can be zero when the model's minimum prompt length is not met.

### Official OpenAI explicit-cache models

- Confirm the request uses an `api.openai.com` base URL and a supported explicit model family.
- The first eligible request should report `cache_write_tokens > 0` through KCode's cache creation
  field.
- A matching request should report `cached_tokens > 0` through KCode's cache read field.

### Older OpenAI and compatible endpoints

- KCode deliberately sends no explicit cache fields.
- Record cached usage only when the endpoint returns it. `None` means unknown or unsupported, not a
  confirmed zero-token cache result.

### DeepSeek

- Context caching is automatic.
- A later matching request should report `prompt_cache_hit_tokens > 0` through KCode's cache read
  field.
- `prompt_cache_miss_tokens` is not a cache write and is intentionally not mapped to creation.

## Failure interpretation

- `None`: the endpoint did not provide a valid field; the request itself may still be successful.
- `0`: the endpoint explicitly reported no cached tokens, commonly because of a miss or minimum
  prefix length.
- HTTP 400 from a compatible endpoint: verify it was classified as automatic mode; KCode must not
  retry the request automatically.
- No second-request hit: verify model, prompt, tools, cache key, request order, TTL and concurrency.

## Qualitative comparison

Run the same pinned model and task once from the commit immediately before this feature and once
from the completed feature. Use disposable worktrees or copies so the two runs do not share modified
files.

| Scenario | Record before and after |
|---|---|
| Find and search for a symbol | Whether `find_files`/`search_code` are used instead of shell search |
| Edit an existing file | Whether `read_file` occurs before `edit_file` |
| Multi-iteration Plan Mode | Every requested tool and whether all actions remain read-only |
| Ask about the current project | Whether directory, platform, date, git, version and model are used accurately |
| Complete a multi-step change | Tool sequence, verification evidence and final answer clarity |

This is a factual record only. Do not assign automatic scores or treat one nondeterministic run as a
statistically meaningful evaluation.
