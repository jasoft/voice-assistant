#!/usr/bin/env bash
# Stop hook: 读取 TTS 总结并发送通知
# 唯一输出到 stdout 的必须是: echo "{}"

LOG_FILE="/tmp/claude_stop_notify.log"

# 初始化日志
echo "=== $(date '+%Y-%m-%d %H:%M:%S') ===" >> "$LOG_FILE"

# 1. 从 transcript 提取最后一个 <tts>...</tts>
if [ -z "$tts_text" ]; then
    echo "[DEBUG] No temp file content, trying transcript..." >> "$LOG_FILE"

    # 从 stdin 读取 hook 输入。手动运行时 stdin 可能是 tty，避免阻塞。
    if [ -t 0 ]; then
        echo "[DEBUG] stdin is tty, skipping read" >> "$LOG_FILE"
        input=""
    else
        input=$(cat)
        echo "[DEBUG] Raw input length: ${#input}" >> "$LOG_FILE"
    fi

    if command -v jq >/dev/null 2>&1; then
        transcript_path=$(echo "$input" | jq -r '.transcript_path // ""' 2>/dev/null)
        echo "[DEBUG] transcript_path: $transcript_path" >> "$LOG_FILE"

        if [ -f "$transcript_path" ]; then
            echo "[DEBUG] transcript exists, reading last session..." >> "$LOG_FILE"

            last_session_id=$(jq -r 'select(has("session_id")) | .session_id' "$transcript_path" 2>/dev/null | tail -n 1)
            if [ -n "$last_session_id" ] && [ "$last_session_id" != "null" ]; then
                echo "[DEBUG] Found last session_id: $last_session_id" >> "$LOG_FILE"
                session_filter="select(.session_id == \"$last_session_id\")"
            else
                echo "[DEBUG] No session_id field found, using full transcript" >> "$LOG_FILE"
                session_filter="."
            fi

            session_text=$(jq -r "$session_filter | .. | .text? // empty" "$transcript_path" 2>/dev/null | sed 's/\r//g')

            tts_text=$(printf '%s\n' "$session_text" | perl -0777 -ne 'while(/<tts>(.*?)<\/tts>/sg){$val=$1; $val =~ s/^\s+|\s+$//g; $last=$val if length $val} print $last')
            if [ -z "$tts_text" ]; then
                echo "[DEBUG] No <tts> found in last session, falling back to last paragraph" >> "$LOG_FILE"
                tts_text=$(printf '%s\n' "$session_text" | awk 'BEGIN{RS=""; ORS="\n\n"} {gsub(/\n+/, " "); gsub(/^[[:space:]]+|[[:space:]]+$/, ""); if(length) paragraph=$0} END{print paragraph}')
            fi

            echo "[DEBUG] Extracted from transcript: $tts_text" >> "$LOG_FILE"
        fi
    fi
fi


# 清理空白字符
tts_text=$(echo "$tts_text" | tr -s '[:space:]' ' ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
echo "[DEBUG] Final tts_text: $tts_text" >> "$LOG_FILE"

# 4. 防止频繁触发 (1秒内不重复)
lock_file="/tmp/claude_tts.lock"
current_time=$(date +%s%N | cut -b1-13)

if [ -f "$lock_file" ]; then
    last_time=$(cat "$lock_file")
    diff=$((current_time - last_time))
    if [ "$diff" -lt 1000 ]; then
        echo "[DEBUG] Rate limited, exiting" >> "$LOG_FILE"
        echo "{}"  # 唯一输出到 stdout
        exit 0
    fi
fi
echo "$current_time" > "$lock_file"

# 5. 后台调用通知脚本 (使用 nohup 确保真正后台)
if [ -n "$tts_text" ]; then
    echo "[DEBUG] Calling notify.sh with text: $tts_text" >> "$LOG_FILE"
    nohup bash -c "/Users/weiwang/Projects/shell/scripts/notify.sh '$tts_text' >> '$LOG_FILE' 2>&1" &
    disown 2>/dev/null
fi

# 6. 系统桌面通知 (后台)
if command -v terminal-notifier >/dev/null 2>&1; then
    terminal-notifier \
        -title "Claude Code" \
        -message "$tts_text" \
        -group "claude-code-hooks" \
        -sound "hero" > /dev/null 2>&1 &
    disown 2>/dev/null
fi

echo "[DEBUG] Done" >> "$LOG_FILE"

# 唯一输出到 stdout: hook 需要的 JSON
echo "{}"
exit 0
