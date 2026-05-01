---
name: ptt-voice
description: PTT 语音助手的命令行工具集，包含录音、意图识别和内存管理。
---

# PTT Voice Assistant CLI


Voice assistant command suite for push-to-talk recording, intent classification, and long-term memory.

## Discovery and Setup

First, verify the installation and environment:

```bash
ptt-voice doctor
```

This returns a JSON report including audio device status and storage backend connectivity.

## Core Commands

### Interactive Voice Session
Start the interactive PTT listener:
```bash
ptt-voice start
```
Use `--debug` for verbose logging of STT and LLM interactions.

### Text-only Test
Inject text directly to test the execution pipeline without recording audio:
```bash
ptt-voice start --text-input "Remind me that my passport is in the top drawer"
```

### Batch Regression
Run intent classification regression tests:
```bash
ptt-voice start --intent-samples-file testdata/intent_samples.jsonl
```

## Storage Management

Use `ptt-storage` for direct manipulation of session history and memories.

### Search Memory
```bash
ptt-storage memory search --query "passport"
```

### List History
```bash
ptt-storage history list --limit 5
```

### Storage Doctor
Verify storage-specific configuration:
```bash
ptt-storage doctor
```

## JSON Policy
All discovery and read commands support or default to JSON output. Errors are returned as `{ "error": "..." }` to stderr or stdout with a non-zero exit code.
