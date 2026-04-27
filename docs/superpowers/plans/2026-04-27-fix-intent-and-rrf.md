# Intent Extraction and RRF Filtering Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve memory query accuracy by extracting date ranges from user queries to avoid FTS/Vector pollution, and restore absolute `min_score` filtering before RRF normalization to block low-quality fallback results.

**Architecture:** 
1. Update the `intent_extractor_config.json` schema to include `start_date` and `end_date`, and instruct the LLM to strip time adverbials from the `query` field.
2. Update `press_to_talk/storage/providers/sqlite_fts.py` to apply absolute confidence filtering (using `min_score`) on candidate items before performing RRF normalization, preventing low-confidence items from being artificially boosted to a score of 1.0.

**Tech Stack:** Python, JSON

---

### Task 1: Update Intent Extractor Schema and Instructions

**Files:**
- Modify: `intent_extractor_config.json`

- [ ] **Step 1: Read the current JSON configuration**
  Read `intent_extractor_config.json` to understand the exact structure.

- [ ] **Step 2: Update the schema and instructions**
  Update the JSON file with the following changes:
  - In `schema.args`, add `"start_date"` and `"end_date"` (ISO format YYYY-MM-DD).
  - Update the description for `schema.args.query` to explicitly mention removing time adverbials.
  - Add an instruction explaining how to extract `start_date` and `end_date` for `find` intents.
  - Update the examples to populate `start_date` and `end_date` correctly, leaving `query` clean.

- [ ] **Step 3: Run intent extraction tests**
  Run `poe test tests/test_intent_time_parsing.py` (if it exists) or general tests to ensure JSON is valid.

- [ ] **Step 4: Commit changes**
  Commit the JSON update.

---

### Task 2: Implement absolute filtering before RRF normalization

**Files:**
- Modify: `press_to_talk/storage/providers/sqlite_fts.py`
- Test: `tests/test_storage_search_toggles.py` or similar

- [ ] **Step 1: Write a failing test or identify testing strategy**
  Since there are existing tests like `poe test`, we will ensure we don't break existing behavior but fix the `min_score` logic.

- [ ] **Step 2: Modify `find` method in `sqlite_fts.py`**
  In `press_to_talk/storage/providers/sqlite_fts.py`, locate the RRF merging section (around line 930+).
  Implement absolute pre-filtering before RRF normalization using `min_score`:
  ```python
  # Filter `merged` dictionary based on absolute min_score before RRF
  if min_score > 0:
      filtered_merged = {}
      for k, item in merged.items():
          pass_filter = False
          # Check vector score
          if item.get("embedding_score") and item["embedding_score"] >= min_score:
              pass_filter = True
          # Check FTS confidence (using 0-based index)
          elif "fts_rank" in item and _fts_confidence(item["fts_rank"] - 1) >= min_score:
              pass_filter = True
              
          if pass_filter:
              filtered_merged[k] = item
      merged = filtered_merged
  ```
  Apply this right before the RRF loop.

- [ ] **Step 3: Update RRF normalization logic**
  Ensure that if `merged` is empty after filtering, we return an empty list early.
  Keep the existing normalization, but now it only applies to items that passed the absolute confidence check.

- [ ] **Step 4: Run all tests**
  Run `poe test` to ensure no regressions and that the tests pass.

- [ ] **Step 5: Commit changes**
  Commit the Python updates.
