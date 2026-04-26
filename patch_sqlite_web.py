#!/usr/bin/env python3
"""
启动 sqlite_web 并自动加载 libsimple.so 扩展。
通过 monkey-patch peewee 的 SqliteDatabase，使每个连接在创建时自动加载扩展。
"""
import sys
import os

SIMPLE_SO_PATH = "/app/third_party/simple/libsimple.so"

def patch_peewee():
    """Monkey-patch peewee 的 SqliteDatabase，使连接自动加载 libsimple.so"""
    try:
        from peewee import SqliteDatabase

        original_connect = SqliteDatabase._connect

        def patched_connect(self, *args, **kwargs):
            # 调用原始 _connect 建立连接
            result = original_connect(self, *args, **kwargs)
            # 获取原始 sqlite3 连接并加载扩展
            try:
                raw_conn = self._state.conn
                raw_conn.enable_load_extension(True)
                raw_conn.load_extension(SIMPLE_SO_PATH)
            except Exception as e:
                print(f"Warning: Failed to load simple extension: {e}", file=sys.stderr)
            return result

        SqliteDatabase._connect = patched_connect
        print(f"Patched peewee SqliteDatabase to load {SIMPLE_SO_PATH}")
    except ImportError:
        print("Warning: peewee not found, skipping patch", file=sys.stderr)

def main():
    patch_peewee()

    # 将命令行参数传递给 sqlite_web
    from sqlite_web.__main__ import main as sqlite_web_main
    sys.argv[0] = "sqlite_web"  # 修复程序名
    sqlite_web_main()

if __name__ == "__main__":
    main()
