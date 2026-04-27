#!/usr/bin/env bash

LOG_FILE="/tmp/claude_stop_hook.log"
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG_FILE"

# 从 stdin 读取当前会话的上下文信息
input=$(cat)
echo "[DEBUG] Raw input: $input" >> "$LOG_FILE"

tts_text=""

# 优先从临时文件读取（Claude 输出 <tts> 后会写入这个文件）
TTS_TEMP_FILE="/tmp/claude_last_tts.txt"
if [ -f "$TTS_TEMP_FILE" ]; then
    tts_text=$(cat "$TTS_TEMP_FILE" | tr -d '\n' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    echo "[DEBUG] Read from temp file: $tts_text" >> "$LOG_FILE"
    # 读取后清空文件
    > "$TTS_TEMP_FILE"
fi

# 如果临时文件没有内容，再从 transcript 提取
if [ -z "$tts_text" ]; then
    echo "[DEBUG] No temp file content, trying transcript..." >> "$LOG_FILE"

    if command -v jq >/dev/null 2>&1; then
        transcript_path=$(echo "$input" | jq -r '.transcript_path // ""')
        echo "[DEBUG] transcript_path: $transcript_path" >> "$LOG_FILE"

        if [ -f "$transcript_path" ]; then
            all_text=$(cat "$transcript_path" | jq -c 'select(.type=="assistant")' 2>/dev/null | tail -100 | jq -r '.message.content[]? | select(.type=="text") | .text' 2>/dev/null)
            echo "[DEBUG] all_text length: ${#all_text}" >> "$LOG_FILE"

            if [ -n "$all_text" ]; then
                tts_text=$(echo "$all_text" | perl -0777 -ne 'while(/(?:<tts>|<ttts>)(.*?)(?:<\/tts>|<\/ttts>)/sg){$val=$1; $val =~ s/^\s+|\s+$//g; if($val){$last=$val}} print $last')
                echo "[DEBUG] Extracted tts_text from transcript: $tts_text" >> "$LOG_FILE"
            fi
        fi
    fi
fi

# Fallback: git summary
if [ -z "$tts_text" ]; then
    echo "[DEBUG] No <tts> tag found, using git summary" >> "$LOG_FILE"
    PROJECT_DIR=$(git rev-parse --show-toplevel 2>/dev/null || echo "$(pwd)")
    cd "$PROJECT_DIR" 2>/dev/null || true

    UNSTAGED=$(git diff --stat 2>/dev/null || echo "")
    STAGED=$(git diff --cached --stat 2>/dev/null || echo "")
    UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null | wc -l | tr -d ' ' || echo "0")
    LAST_COMMIT=$(git log -1 --pretty=format:"%h %s" 2>/dev/null || echo "")

    if [ -n "$UNSTAGED" ] || [ -n "$STAGED" ] || [ "$UNTRACKED" != "0" ]; then
        tts_text="有未提交的改动"
        [ -n "$UNSTAGED" ] && tts_text="$tts_text, 未暂存 $(echo "$UNSTAGED" | wc -l | tr -d ' ') 文件"
        [ -n "$STAGED" ] && tts_text="$tts_text, 已暂存 $(echo "$STAGED" | wc -l | tr -d ' ') 文件"
        [ "$UNTRACKED" != "0" ] && tts_text="$tts_text, 未跟踪 $UNTRACKED 文件"
    elif [ -n "$LAST_COMMIT" ]; then
        tts_text="最近提交: $LAST_COMMIT"
    else
        tts_text="任务已完成"
    fi
    echo "[DEBUG] Fallback tts_text: $tts_text" >> "$LOG_FILE"
fi

# 清理空白字符
tts_text=$(echo "$tts_text" | tr -s '[:space:]' ' ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
echo "[DEBUG] Final tts_text: $tts_text" >> "$LOG_FILE"

# 语音提示 (后台执行)
lock_file="/tmp/claude_tts.lock"
current_time=$(date +%s%N | cut -b1-13)

if [ -f "$lock_file" ]; then
    last_time=$(cat "$lock_file")
    diff=$((current_time - last_time))
    if [ "$diff" -lt 1000 ]; then
        echo "[DEBUG] Rate limited, exiting" >> "$LOG_FILE"
        exit 0
    fi
fi
echo "$current_time" > "$lock_file"

# 调用通知脚本
echo "[DEBUG] Calling notify.sh with text: $tts_text" >> "$LOG_FILE"
~/Projects/shell/scripts/notify.sh "$tts_text" >> "$LOG_FILE" 2>&1 &

# 系统桌面通知
terminal-notifier \
    -title "Claude Code" \
    -message "$tts_text" \
    -group "claude-code-hooks" \
    -sound "hero" > /dev/null 2>&1

echo "[DEBUG] Done" >> "$LOG_FILE"
echo "{}"
exit 0
