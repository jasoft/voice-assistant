from __future__ import annotations

import os
import time
import unittest

from press_to_talk.storage.service import MEM0_APP_ID, Mem0RememberStore, create_mem0_client


def e2e_enabled() -> bool:
    return os.environ.get("PTT_RUN_E2E", "").strip() == "1"


@unittest.skipUnless(e2e_enabled(), "set PTT_RUN_E2E=1 to run mem0 e2e tests")
class Mem0E2ETests(unittest.TestCase):
    def setUp(self) -> None:
        api_key = os.environ.get("MEM0_API_KEY", "").strip()
        if not api_key:
            self.skipTest("MEM0_API_KEY is required for mem0 e2e tests")
        self.client = create_mem0_client(api_key)
        self.user_id = f"soj-e2e-{int(time.time() * 1000)}"
        self.store = Mem0RememberStore(client=self.client, user_id=self.user_id)
        self.created_ids: list[str] = []

    def tearDown(self) -> None:
        for memory_id in self.created_ids:
            try:
                self.client.delete(memory_id)
            except Exception:
                pass

    def test_add_find_and_list_round_trip(self) -> None:
        unique_suffix = str(int(time.time() * 1000))
        memory_text = f"e2e测试护照在书房抽屉里-{unique_suffix}"
        original_text = f"帮我记住{memory_text}"

        add_result = self.store.add(memory=memory_text, original_text=original_text)
        self.assertIn("✅ 已记录", add_result)

        # mem0 托管端搜索存在轻微最终一致性，给它一个很短的重试窗口。
        search_json = ""
        list_json = ""
        for _ in range(6):
            search_json = self.store.find(query=memory_text)
            list_json = self.store.list_recent(limit=10)
            if memory_text in search_json and memory_text in list_json:
                break
            time.sleep(1)

        self.assertIn(memory_text, search_json)
        self.assertIn(f'"app_id": "{MEM0_APP_ID}"', search_json)
        self.assertIn(f'"user_id": "{self.user_id}"', search_json)
        self.assertIn(memory_text, list_json)
        self.assertIn(f'"app_id": "{MEM0_APP_ID}"', list_json)

        search_response = self.client.search(
            memory_text,
            filters={"AND": [{"user_id": self.user_id}, {"app_id": MEM0_APP_ID}]},
        )
        results = search_response.get("results", search_response if isinstance(search_response, list) else [])
        self.assertTrue(results, "expected at least one mem0 search result")
        first = results[0]
        self.assertEqual(first.get("app_id"), MEM0_APP_ID)
        self.assertEqual(first.get("user_id"), self.user_id)
        memory_id = str(first.get("id") or "").strip()
        self.assertTrue(memory_id)
        self.created_ids.append(memory_id)


if __name__ == "__main__":
    unittest.main()
