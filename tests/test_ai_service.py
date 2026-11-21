from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from creatorscope import create_app
from creatorscope.ai import AIServiceError, build_preview, provider_status, run_analysis, test_connection
from creatorscope.db import connection, initialize_database


class AIServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory(prefix="creatorscope-ai-")
        self.db_path = Path(self.temp_dir.name) / "ai.sqlite3"
        initialize_database(self.db_path)
        with connection(self.db_path) as conn:
            for batch_id, captured in (("batch-a", "2026-11-01T10:00:00"), ("batch-b", "2026-11-02T10:00:00")):
                task_id = "task-" + batch_id
                conn.execute(
                    "INSERT INTO tasks(task_id,task_type,source,status,progress,message,parameters_json,created_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (task_id, "file_import", "pgy_visible", "completed", 100, "fixture", "{}", captured, captured),
                )
                conn.execute(
                    "INSERT INTO batches(batch_id,task_id,source,captured_at,started_at,completed_at,status) VALUES(?,?,?,?,?,?,?)",
                    (batch_id, task_id, "pgy_visible", captured, captured, captured, "completed"),
                )
            conn.execute(
                "INSERT INTO creators(creator_id,pseudonym,creator_name,platform,platform_creator_id,source_key_hash,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?)",
                ("creator-a", "博主 0001", "课程创作者甲", "xiaohongshu", "platform-user-001", "ai-hash-creator", "2026-11-01", "2026-11-02"),
            )
            conn.execute(
                "INSERT INTO notes(note_id,creator_id,note_key_hash,platform_note_id,title,body,topics_json,published_at,note_type,source_url,source,first_seen_at,last_seen_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("note-a", "creator-a", "ai-hash-note", "platform-note-001", "护肤分享", "", json.dumps(["#护肤"], ensure_ascii=False), "2026-11-01", "视频", "https://example.test/note-001", "pgy_visible", "2026-11-01", "2026-11-02"),
            )
            for batch_id, captured, followers, reading in (
                ("batch-a", "2026-11-01T10:00:00", "100", "1000"),
                ("batch-b", "2026-11-02T10:00:00", "120", "1200"),
            ):
                profile = conn.execute(
                    "INSERT INTO records(batch_id,record_type,captured_at,fields_json,fields_hash,creator_id) VALUES(?,?,?,?,?,?)",
                    (batch_id, "creator_profile_sample", captured, json.dumps({"粉丝数": followers}, ensure_ascii=False), "ai-profile-" + batch_id, "creator-a"),
                )
                conn.execute(
                    "INSERT INTO metric_values(record_id,metric_key,raw_value,numeric_value,unit,period,parse_status) VALUES(?,?,?,?,?,?,?)",
                    (profile.lastrowid, "粉丝数", followers, float(followers), "count", "当前粉丝快照", "parsed"),
                )
                note = conn.execute(
                    "INSERT INTO records(batch_id,record_type,captured_at,fields_json,fields_hash,creator_id,note_id) VALUES(?,?,?,?,?,?,?)",
                    (batch_id, "note_detail", captured, json.dumps({"阅读量": reading}, ensure_ascii=False), "ai-note-" + batch_id, "creator-a", "note-a"),
                )
                conn.execute(
                    "INSERT INTO metric_values(record_id,metric_key,raw_value,numeric_value,unit,period,parse_status) VALUES(?,?,?,?,?,?,?)",
                    (note.lastrowid, "阅读量", reading, float(reading), "count", "累计", "parsed"),
                )
            conn.execute(
                "INSERT INTO comment_snapshots(batch_id,note_id,local_comment_id_hash,comment_text,cleaned_text,comment_level,source,captured_at,raw_payload_json) VALUES(?,?,?,?,?,?,?,?,?)",
                ("batch-a", "note-a", "ai-comment-hash", "好用 温和 保湿", "好用 温和 保湿", 1, "visible_public", "2026-11-01T12:00:00", json.dumps({"commenter_name": "评论用户乙", "commenter_id": "comment-user-002", "profile_url": "https://example.test/profile/2"}, ensure_ascii=False)),
            )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_no_key_keeps_preview_available_and_model_call_blocked(self) -> None:
        empty_keys = {
            "OPENAI_API_KEY": "", "DEEPSEEK_API_KEY": "", "ANTHROPIC_API_KEY": "",
            "GEMINI_API_KEY": "", "GOOGLE_API_KEY": "", "CREATORSCOPE_AI_MODEL": "",
        }
        with patch.dict(os.environ, empty_keys):
            status = provider_status("openai_compatible", model="local-model")
            preview = build_preview(self.db_path, analysis_type="creator_summary", creator_ids=["creator-a"], batch_id="batch-a")
            self.assertFalse(status["key_available"])
            self.assertNotIn("openai-test-key", json.dumps(status))
            self.assertEqual(preview["data"]["creators"][0]["creator_name"], "课程创作者甲")
            self.assertEqual(preview["data"]["creators"][0]["platform_creator_id"], "platform-user-001")
            with self.assertRaisesRegex(AIServiceError, "未配置密钥"):
                test_connection(provider="openai_compatible", model="local-model", base_url="https://api.openai.com/v1")

    def test_note_preview_preserves_direct_contact_url_and_full_body(self) -> None:
        body = "联系 me@example.com 或 13800138000，链接 https://example.test/private " + ("正文" * 1800)
        with connection(self.db_path) as conn:
            conn.execute(
                "UPDATE notes SET body=? WHERE note_id=?",
                (body, "note-a"),
            )
        preview = build_preview(self.db_path, analysis_type="note_summary", note_ids=["note-a"], batch_id="batch-a", include_raw_text=True)
        serialized = json.dumps(preview["data"], ensure_ascii=False)
        note = preview["data"]["notes"][0]
        self.assertIn("me@example.com", serialized)
        self.assertIn("13800138000", serialized)
        self.assertIn("https://example.test/private", serialized)
        self.assertEqual(note["body"], body)
        self.assertEqual(note["platform_note_id"], "platform-note-001")
        self.assertEqual(note["source_url"], "https://example.test/note-001")

    def test_note_preview_excludes_body_by_default_without_changing_local_value(self) -> None:
        body = "只在正文出现的完整原始内容 contact@example.test"
        with connection(self.db_path) as conn:
            conn.execute("UPDATE notes SET body=? WHERE note_id=?", (body, "note-a"))
        preview = build_preview(self.db_path, analysis_type="note_summary", note_ids=["note-a"], batch_id="batch-a")
        serialized = json.dumps(preview["data"], ensure_ascii=False)
        self.assertNotIn(body, serialized)
        self.assertIsNone(preview["data"]["notes"][0]["body"])
        self.assertFalse(preview["data"]["notes"][0]["raw_text_included"])
        self.assertFalse(preview["include_raw_text"])
        self.assertIn("不会进入本次模型输入", preview["data_notice"])
        with connection(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT body FROM notes WHERE note_id='note-a'").fetchone()[0], body)

    def test_comment_preview_preserves_comment_text_and_source_fields(self) -> None:
        preview = build_preview(
            self.db_path,
            analysis_type="comment_topics",
            note_ids=["note-a"],
            batch_id="batch-a",
            include_raw_text=True,
        )
        serialized = json.dumps(preview["data"], ensure_ascii=False)
        self.assertIn("comment_count_total", serialized)
        self.assertIn("好用 温和 保湿", serialized)
        self.assertIn("comment_text", serialized)
        self.assertIn("评论用户乙", serialized)
        self.assertIn("comment-user-002", serialized)
        self.assertIn("https://example.test/profile/2", serialized)
        self.assertIn("local_comment_id", serialized)

    def test_comment_preview_excludes_raw_comment_and_identity_fields_by_default(self) -> None:
        preview = build_preview(
            self.db_path,
            analysis_type="comment_topics",
            note_ids=["note-a"],
            batch_id="batch-a",
        )
        serialized = json.dumps(preview["data"], ensure_ascii=False)
        self.assertEqual(preview["data"]["comment_count_total"], 1)
        self.assertNotIn("comments", preview["data"])
        self.assertNotIn("好用 温和 保湿", serialized)
        self.assertNotIn("评论用户乙", serialized)
        self.assertNotIn("comment-user-002", serialized)
        self.assertFalse(preview["include_raw_text"])

    def test_provider_adapters_use_provider_auth_headers_without_leaking_key_to_url(self) -> None:
        keys = {
            "OPENAI_API_KEY": "openai-test-key",
            "DEEPSEEK_API_KEY": "deepseek-test-key",
            "ANTHROPIC_API_KEY": "claude-test-key",
            "GEMINI_API_KEY": "gemini-test-key",
        }
        responses = {
            "openai_compatible": {"choices": [{"message": {"content": "OK"}}]},
            "deepseek": {"choices": [{"message": {"content": "OK"}}]},
            "claude": {"content": [{"type": "text", "text": "OK"}]},
            "gemini": {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]},
        }
        config = {
            "openai_compatible": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
            "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
            "claude": ("https://api.anthropic.com", "ANTHROPIC_API_KEY"),
            "gemini": ("https://generativelanguage.googleapis.com/v1beta", "GEMINI_API_KEY"),
        }
        with patch.dict(os.environ, keys):
            for provider, (base_url, env_name) in config.items():
                with self.subTest(provider=provider), patch("creatorscope.ai._request_json", return_value=responses[provider]) as request:
                    result = test_connection(provider=provider, model="test-model", base_url=base_url)
                    self.assertEqual(result, "OK")
                    url, headers, _payload = request.call_args.args
                    self.assertNotIn(keys[env_name], url)
                    if provider == "gemini":
                        self.assertEqual(headers["x-goog-api-key"], keys[env_name])
                        self.assertNotIn("Authorization", headers)
                    else:
                        self.assertEqual(headers["Authorization"], "Bearer " + keys[env_name])
                    if provider == "claude":
                        self.assertEqual(headers["anthropic-version"], "2026-10-21")

    def test_base_url_blocks_remote_http_and_embedded_credentials(self) -> None:
        with self.assertRaisesRegex(AIServiceError, "HTTPS"):
            provider_status("openai_compatible", base_url="http://models.example.test/v1", model="model")
        with self.assertRaisesRegex(AIServiceError, "有效的 HTTPS"):
            provider_status("openai_compatible", base_url="https://user:pass@models.example.test/v1", model="model")
        self.assertEqual(provider_status("openai_compatible", base_url="http://127.0.0.1:11434/v1", model="model")["base_url"], "http://127.0.0.1:11434/v1")

    def test_ai_routes_allow_offline_preview_and_require_explicit_upload_confirmation(self) -> None:
        empty_keys = {
            "OPENAI_API_KEY": "", "DEEPSEEK_API_KEY": "", "ANTHROPIC_API_KEY": "",
            "GEMINI_API_KEY": "", "GOOGLE_API_KEY": "",
        }
        app = create_app({"DATABASE_PATH": self.db_path, "BASELINE_PATH": self.db_path.parent / "missing.json", "TESTING": True})
        client = app.test_client()
        with patch.dict(os.environ, empty_keys):
            self.assertEqual(client.get("/ai").status_code, 200)
            config = client.get("/api/ai/config?provider=gemini").get_json()
            self.assertFalse(config["key_available"])
            preview = client.post("/api/ai/preview", json={
                "analysis_type": "creator_summary", "creator_ids": ["creator-a"], "batch_id": "batch-a",
            })
            self.assertEqual(preview.status_code, 200)
            self.assertFalse(preview.get_json()["include_raw_text"])
            rejected = client.post("/api/ai/analyze", json={
                "provider": "openai_compatible", "model": "model", "analysis_type": "creator_summary",
                "creator_ids": ["creator-a"], "batch_id": "batch-a",
            })
            self.assertEqual(rejected.status_code, 400)
            not_configured = client.post("/api/ai/analyze", json={
                "confirm_upload": True, "provider": "openai_compatible", "model": "model",
                "analysis_type": "creator_summary", "creator_ids": ["creator-a"], "batch_id": "batch-a",
            })
            self.assertEqual(not_configured.status_code, 200)
            self.assertEqual(not_configured.get_json()["status"], "not_configured")
            no_key = client.post("/api/ai/test", json={
                "provider": "openai_compatible", "model": "model", "base_url": "https://api.openai.com/v1",
            })
            self.assertEqual(no_key.status_code, 200)
            self.assertEqual(no_key.get_json()["status"], "not_configured")

    def test_ai_preview_route_includes_full_note_body_only_when_requested(self) -> None:
        body = "模型输入选项对应的完整笔记正文"
        with connection(self.db_path) as conn:
            conn.execute("UPDATE notes SET body=? WHERE note_id=?", (body, "note-a"))
        app = create_app({"DATABASE_PATH": self.db_path, "BASELINE_PATH": self.db_path.parent / "missing.json", "TESTING": True})
        client = app.test_client()
        response = client.post("/api/ai/preview", json={
            "analysis_type": "note_summary", "note_ids": ["note-a"], "batch_id": "batch-a",
            "include_raw_text": True,
        })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        payload = response.get_json()
        self.assertTrue(payload["include_raw_text"])
        self.assertTrue(payload["data"]["notes"][0]["raw_text_included"])
        self.assertEqual(payload["data"]["notes"][0]["body"], body)

    def test_confirmed_mock_analysis_persists_local_report_metadata_and_raw_facts(self) -> None:
        reports_dir = self.db_path.parent / "ai reports"
        app = create_app({
            "DATABASE_PATH": self.db_path,
            "AI_REPORTS_PATH": reports_dir,
            "BASELINE_PATH": self.db_path.parent / "missing.json",
            "TESTING": True,
        })
        client = app.test_client()
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only-key"}), patch("creatorscope.run_analysis", return_value="基于本地样例的 Mock 输出") as model:
            response = client.post("/api/ai/analyze", json={
                "confirm_upload": True,
                "provider": "openai_compatible",
                "model": "test-model",
                "analysis_type": "creator_summary",
                "creator_ids": ["creator-a"],
                "note_ids": [],
                "batch_id": "batch-a",
                "include_raw_text": False,
            })
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        result = response.get_json()
        self.assertTrue(result["report_saved"])
        self.assertFalse(result["include_raw_text"])
        self.assertEqual(result["input_batch_id"], "batch-a")
        self.assertEqual(result["model"], "test-model")
        self.assertIn("analysis_at", result)
        self.assertEqual(result["model_output"], "基于本地样例的 Mock 输出")
        self.assertEqual(model.call_count, 1)
        stored = client.get(f"/api/ai/reports/{result['run_id']}")
        self.assertEqual(stored.status_code, 200)
        self.assertEqual(stored.get_json()["facts"], result["facts"])
        self.assertNotIn("test-only-key", stored.get_data(as_text=True))
        history = client.get("/api/ai/reports").get_json()["reports"]
        self.assertEqual(history[0]["run_id"], result["run_id"])
        self.assertEqual(history[0]["input_batch_id"], "batch-a")
        self.assertFalse(history[0]["include_raw_text"])
        self.assertEqual(client.get("/api/ai/reports/../secret").status_code, 404)


if __name__ == "__main__":
    unittest.main()
