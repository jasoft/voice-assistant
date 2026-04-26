#!/usr/bin/env bash

# 从 stdin 读取当前会话的上下文信息 (JSON 格式)
input=$(cat)

if command -v jq >/dev/null 2>&1; then
    # 解析字段
    transcript_path=$(echo "$input" | jq -r '.transcript_path // ""')
    hook_timestamp=$(echo "$input" | jq -r '.timestamp // ""')

    # 初始化 tts_text
    tts_text=""

    # 从 transcript 中提取最后一个 <tts> 标签内容
    if [ -f "$transcript_path" ]; then
        # 提取最后一个 assistant 消息的 text 内容
        last_text=$(cat "$transcript_path" | jq -r 'select(.type=="assistant") | .message.content[] | select(.type=="text") | .text' 2>/dev/null | tail -c 10000)

        if [ -n "$last_text" ]; then
            # 使用 perl 提取最后一个 <tts>...</tts> 标签内容（参考 turn_end.sh）
            tts_text=$(echo "$last_text" | perl -0777 -ne 'while(/<tts>(.*?)<\/tts>/sg){$val=$1; $val =~ s/^\s+|\s+$//g; if($val){$last=$val}} print $last')
        fi
    fi

    # 如果没有找到 <tts> 标签，使用 git 改动总结作为兜底
    if [ -z "$tts_text" ]; then
        # 获取项目根目录
        PROJECT_DIR=$(git rev-parse --show-toplevel 2>/dev/null || echo "$(pwd)")
        cd "$PROJECT_DIR" 2>/dev/null || true

        # 生成改动总结
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
    fi

    # 清理空白字符，转为单行
    tts_text=$(echo "$tts_text" | tr -s '[:space:]' ' ' | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')

    # 兜底：如果 tts_text 仍为空，使用默认语
    if [ -z "$tts_text" ] || [ "$tts_text" == "null" ]; then
        tts_text="处理完成，请大王吩咐"
    fi

    # --- 耗时判定逻辑 ---
    # 如果耗时太短（小于 30 秒），只发系统通知，跳过 TTS
    if [ -f "$transcript_path" ] && [ -n "$hook_timestamp" ]; then
        # 提取最后一条用户消息的时间戳
        user_timestamp=$(jq -r '[.messages[] | select(.type=="user")] | last | .timestamp' "$transcript_path" 2>/dev/null)

        if [ -n "$user_timestamp" ] && [ "$user_timestamp" != "null" ]; then
            # 转换为 Unix 时间戳 (秒)
            start_sec=$(date -j -f "%Y-%m-%dT%H:%M:%S" "${user_timestamp%.*}" "+%s" 2>/dev/null)
            end_sec=$(date -j -f "%Y-%m-%dT%H:%M:%S" "${hook_timestamp%.*}" "+%s" 2>/dev/null)

            if [ -n "$start_sec" ] && [ -n "$end_sec" ]; then
                duration=$((end_sec - start_sec))
                threshold=30

                if [ "$duration" -lt "$threshold" ]; then
                    # 耗时太短，跳过 TTS，只保留系统通知
                    terminal-notifier \
                        -title "Claude Code (Fast)" \
                        -message "$tts_text" \
                        -group "claude-code-hooks" \
                        -sound "hero" > /dev/null 2>&1
                    echo "{}"
                    exit 0
                fi
            fi
        fi
    fi
else
    tts_text="任务已完成"
fi

# 语音提示 (后台执行)
# 使用简单文件锁机制防止极短时间内的重复触发
lock_file="/tmp/claude_tts.lock"
current_time=$(date +%s%N | cut -b1-13)

if [ -f "$lock_file" ]; then
    last_time=$(cat "$lock_file")
    diff=$((current_time - last_time))
    # 如果两次触发间隔小于 1000 毫秒，则忽略这一次
    if [ "$diff" -lt 1000 ]; then
        exit 0
    fi
fi
echo "$current_time" > "$lock_file"

# 调用通知脚本
~/Projects/shell/scripts/notify.sh "$tts_text" 2>/dev/null &

# 系统桌面通知
terminal-notifier \
    -title "Claude Code" \
    -message "$tts_text" \
    -group "claude-code-hooks" \
    -sound "hero" > /dev/null 2>&1

# 必须输出 JSON
echo "{}"
exit 0
