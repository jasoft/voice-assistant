#!/usr/bin/env python3
"""
启动 sqlite_web 并自动加载 libsimple.so 扩展。
通过 monkey-patch peewee 的 SqliteDatabase._connect，使每个连接在创建时自动加载扩展。
"""
import sys

SIMPLE_SO_PATH = "/app/third_party/simple/libsimple.so"

def patch_peewee():
    """Monkey-patch peewee 的 SqliteDatabase._connect，在连接建立后立即加载扩展"""
    from peewee import SqliteDatabase

    original_connect = SqliteDatabase._connect

    def patched_connect(self, *args, **kwargs):
        # 调用原始 _connect 获取连接对象
        conn = original_connect(self, *args, **kwargs)
        # 在连接上直接加载扩展
        try:
            conn.enable_load_extension(True)
            conn.load_extension(SIMPLE_SO_PATH)
            sys.stderr.write(f"[patch] Loaded {SIMPLE_SO_PATH}\n")
        except Exception as e:
            sys.stderr.write(f"[patch] Warning: Failed to load simple extension: {e}\n")
        return conn

    SqliteDatabase._connect = patched_connect
    sys.stderr.write(f"[patch] Patched peewee SqliteDatabase._connect\n")

def main():
    patch_peewee()

    # 启动 sqlite_web
    from sqlite_web.__main__ import main as sqlite_web_main
    sys.argv[0] = "sqlite_web"
    sqlite_web_main()

if __name__ == "__main__":
    main()
