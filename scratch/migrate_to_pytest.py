import os
import re

def migrate_content(content):
    # 1. 移除 (unittest.TestCase)
    content = re.sub(r'class ([a-zA-Z0-9_]+)\(unittest\.TestCase\):', r'class \1:', content)
    
    # 2. 转换 assertEqual(a, b) -> assert a == b
    # 处理逻辑：找到 self.assertEqual(，然后向后找平衡的括号
    def find_balanced(s, start_index):
        count = 0
        for i in range(start_index, len(s)):
            if s[i] == '(':
                count += 1
            elif s[i] == ')':
                count -= 1
                if count == 0:
                    return i
        return -1

    def replace_calls(content, func_prefix, op):
        offset = 0
        while True:
            match = re.search(func_prefix + r'\(', content[offset:])
            if not match:
                break
            
            start_pos = offset + match.start()
            open_paren = offset + match.end() - 1
            close_paren = find_balanced(content, open_paren)
            
            if close_paren == -1:
                offset = open_paren + 1
                continue
            
            # 提取括号里的内容
            args_str = content[open_paren + 1 : close_paren]
            
            # 这里有个难点：如何拆分两个参数？
            # 我们假设第一个参数是简单的，直到第一个逗号且不在嵌套括号内
            comma_pos = -1
            inner_count = 0
            for j in range(len(args_str)):
                if args_str[j] == '(': inner_count += 1
                elif args_str[j] == ')': inner_count -= 1
                elif args_str[j] == '[': inner_count += 1
                elif args_str[j] == ']': inner_count -= 1
                elif args_str[j] == '{': inner_count += 1
                elif args_str[j] == '}': inner_count -= 1
                elif args_str[j] == ',' and inner_count == 0:
                    comma_pos = j
                    break
            
            if comma_pos != -1:
                arg1 = args_str[:comma_pos].strip()
                arg2 = args_str[comma_pos+1:].strip()
                new_text = f"assert {arg1} {op} {arg2}"
                content = content[:start_pos] + new_text + content[close_paren+1:]
                offset = start_pos + len(new_text)
            else:
                offset = open_paren + 1
        return content

    content = replace_calls(content, r'self\.assertEqual', '==')
    content = replace_calls(content, r'self\.assertIn', 'in')
    content = replace_calls(content, r'self\.assertIsNone', 'is None') # 这里逻辑不对，IsNone 只有一个参数
    
    # 单参数的
    content = re.sub(r'self\.assertTrue\((.*)\)', r'assert \1', content)
    content = re.sub(r'self\.assertIsNone\((.*)\)', r'assert \1 is None', content)
    content = re.sub(r'self\.assertIsNotNone\((.*)\)', r'assert \1 is not None', content)
    
    return content

# 针对报错的文件运行
files = [
    'tests/test_gui_events.py',
    'tests/test_run_gui_script.py',
    'tests/test_execution_modes.py'
]

for filepath in files:
    if os.path.exists(filepath):
        with open(filepath, 'r') as f:
            c = f.read()
        new_c = migrate_content(c)
        with open(filepath, 'w') as f:
            f.write(new_c)
