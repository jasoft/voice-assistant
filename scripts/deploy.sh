#!/bin/bash

# ==============================================================================
# Voice Assistant Deployment Script
# 
# This script syncs local changes to GitHub and triggers a remote build/deploy
# on the Docker server.
# ==============================================================================

set -e

# 1. Local Sync & Version Bump
echo "🚀 Step 1: Checking local workspace..."
if [[ -n $(git status --porcelain) ]]; then
    echo "❌ Local workspace is dirty. Refusing to stage unrelated files."
    echo "   Review the changes, then commit them or add intentional ignores."
    git status --short
    exit 1
else
    echo "✅ Local workspace is clean."
fi

# Automatically bump version (e.g. 0.1.0 -> 0.1.1) and push
NEW_VERSION=$(python3 scripts/bump_version.py)
echo "🏷️  Bumped version to: v${NEW_VERSION}"
git add VERSION pyproject.toml
git commit -m "chore(release): bump version to v${NEW_VERSION}"
git push origin main
echo "✅ Pushed release commit to GitHub."


# 2. Remote Deploy
echo "🌐 Step 2: Triggering remote deployment on 'docker' host..."
# DSH runtime settings are ignored because they hold machine-local state. Patch
# only the non-secret default-model line before recreating containers, so the
# new process starts with the selected model.
ssh docker "cd ~/voice-assistant && git fetch origin main && git checkout main && git reset --hard origin/main && git clean -fd && ./scripts/set_dsh_model.sh fast && docker compose down && docker compose up -d --build"

# 3. Verification
echo "🔍 Step 3: Verifying service status..."
ssh docker "cd ~/voice-assistant && docker compose ps"

echo "✨ Deployment completed successfully!"
