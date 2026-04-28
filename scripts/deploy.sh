#!/bin/bash

# ==============================================================================
# Voice Assistant Deployment Script
# 
# This script syncs local changes to GitHub and triggers a remote build/deploy
# on the Docker server.
# ==============================================================================

set -e

# 1. Local Sync
echo "🚀 Step 1: Checking local changes..."
if [[ -n $(git status --porcelain) ]]; then
    echo "📝 Committing and pushing local changes to main..."
    git add .
    git commit -m "chore: sync local changes before deployment"
    git push origin main
else
    echo "✅ Local workspace is clean, everything up-to-date."
fi

# 2. Remote Deploy
echo "🌐 Step 2: Triggering remote deployment on 'docker' host..."
ssh docker "cd ~/voice-assistant && git reset --hard && git pull && docker compose up -d --build"

# 3. Verification
echo "🔍 Step 3: Verifying service status..."
ssh docker "cd ~/voice-assistant && docker compose ps"

echo "✨ Deployment completed successfully!"
