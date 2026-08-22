#!/bin/bash

# ==============================================================================
# Voice Assistant Deployment Script
# 
# This script syncs local changes to GitHub and triggers a remote build/deploy
# on the Docker server.
# ==============================================================================

set -e

# 1. Local Sync
echo "🚀 Step 1: Checking local workspace..."
if [[ -n $(git status --porcelain) ]]; then
    echo "❌ Local workspace is dirty. Refusing to stage unrelated files."
    echo "   Review the changes, then commit them or add intentional ignores."
    git status --short
    exit 1
else
    echo "✅ Local workspace is clean, everything up-to-date."
fi

# 2. Remote Deploy
echo "🌐 Step 2: Triggering remote deployment on 'docker' host..."
# DSH runtime settings are ignored because they hold machine-local state. Patch
# only the non-secret default-model line before recreating containers, so the
# new process starts with the selected model.
ssh docker "cd ~/voice-assistant && git reset --hard && git clean -fd && git pull && ./scripts/set_dsh_model.sh free && docker compose down && docker compose up -d --build"

# 3. Verification
echo "🔍 Step 3: Verifying service status..."
ssh docker "cd ~/voice-assistant && docker compose ps"

echo "✨ Deployment completed successfully!"
