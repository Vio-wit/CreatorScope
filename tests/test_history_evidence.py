from __future__ import annotations

import csv
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from creatorscope.history_evidence import import_file, quality_report, trace_record


class HistoryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "isolated.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def make_json(self, name, value):
        p = self.root / name
        p.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return p

    def rows(self, sql):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute(sql).fetchall()

    def test_repeat_and_identical_file_preserve_paths_and_source_rows(self):
        one = self.make_json("one.json", [{"博主昵称": "甲", "小红书号": "x1", "粉丝数": "1.2w"}])
        other = self.root / "other.json"
        other.write_bytes(one.read_bytes())
        self.assertEqual(import_file(self.db, one, "first")["parsed"], 1)
        self.assertEqual(import_file(self.db, one, "again")["status"], "duplicate")
        self.assertEqual(import_file(self.db, other, "copy")["status"], "duplicate")
        self.assertEqual(self.rows("SELECT COUNT(*) FROM sources")[0][0], 1)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM source_paths")[0][0], 2)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM source_records")[0][0], 1)
        self.assertEqual(json.loads(self.rows("SELECT raw_json FROM source_records")[0][0])["粉丝数"], "1.2w")
        self.assertEqual(self.rows("SELECT numeric_value FROM field_evidence WHERE target_field='followers'")[0][0], 12000)
        self.assertEqual(sum(x['duplicate_records'] for x in quality_report(self.db)['runs']), 2)
        record_id = self.rows('SELECT record_id FROM source_records')[0][0]
        self.assertEqual(len(trace_record(self.db, record_id)['paths']), 2)
        self.assertEqual(trace_record(self.db, record_id)['record']['row_number'], 1)

    def test_different_files_preserve_duplicate_row_provenance(self):
        one = self.make_json('a.json', [{'博主昵称':'甲','小红书号':'a'}])
        two = self.make_json('b.json', [{'博主昵称':'甲','小红书号':'a'}, {'博主昵称':'乙'}])
        import_file(self.db,one,'first'); import_file(self.db,two,'second')
        self.assertEqual(self.rows('SELECT COUNT(*) FROM source_records')[0][0],3)
        self.assertEqual(self.rows('SELECT SUM(duplicate_content) FROM source_records')[0][0],1)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM identities WHERE status='identified'")[0][0],1)

    def test_identity_conflict_alias_and_missing_ids(self):
        p = self.make_json("ids.json", [
            {"博主昵称": "甲", "小红书号": "a", "蒲公英用户ID": "p1"},
            {"博主昵称": "甲", "小红书号": "b"},
            {"博主昵称": "甲的新名字", "小红书号": "a"},
            {"博主昵称": "无编号"},
            {"博主昵称": "冲突", "小红书号": "a", "蒲公英用户ID": "p2"},
            {"博主昵称": "桥接冲突", "小红书号": "b", "蒲公英用户ID": "p1"},
        ])
        import_file(self.db, p, "identity")
        identities = self.rows("SELECT identity_id,identity_status FROM source_records ORDER BY row_number")
        self.assertNotEqual(identities[0][0], identities[1][0])
        self.assertEqual(identities[0][0], identities[2][0])
        self.assertEqual(identities[3][1], "pending")
        self.assertEqual(identities[4][1], "conflict")
        self.assertEqual(identities[5][1], "conflict")
        self.assertEqual(self.rows("SELECT value FROM identity_keys WHERE key_type='creator_id'"), [("p1",)])
        self.assertEqual(self.rows("SELECT count(*) FROM identity_aliases WHERE identity_id=(SELECT identity_id FROM source_records WHERE row_number=1)")[0][0], 2)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM identity_claims")[0][0], 8)

    def test_price_missing_null_zero_period_and_unknown_capture(self):
        p = self.make_json("values.json", [{"博主昵称":"甲", "粉丝数":0, "粉丝数统计周期":"近30日", "阅读中位数（日常）":"--", "互动中位数（日常）":None, "全部报价":"¥1,100起", "图文笔记一口价":"¥3,000", "视频笔记一口价":"4000", "统计周期":"不能传播到其他字段"}])
        import_file(self.db, p, "values")
        rows = {r[0]: r[1:] for r in self.rows("SELECT target_field,numeric_value,unit,parse_status,period FROM field_evidence")}
        self.assertEqual(rows['followers'], (0.0, 'count', 'parsed', '近30日'))
        self.assertEqual(rows['daily_interaction_median'][2], 'null')
        self.assertEqual(rows['daily_read_median'][2], 'displayed_unavailable')
        self.assertEqual(rows['all_price_starting'][:3], (1100.0, 'CNY', 'starting_price'))
        self.assertEqual(rows['image_fixed_price'][:3], (3000.0, 'CNY', 'parsed'))
        self.assertEqual(rows['video_fixed_price'][1], None)
        self.assertIsNone(rows['video_fixed_price'][3])
        self.assertEqual(self.rows("SELECT capture_time FROM source_records"), [(None,)])

    def test_jsonl_error_and_csv_multiline_preserve_positions(self):
        p = self.root / 'raw.jsonl'
        p.write_text('{"title":"一"}\ninvalid\n', encoding='utf-8')
        result = import_file(self.db, p, 'jsonl')
        self.assertEqual((result['records'],result['parsed'],result['errors']), (2,1,1))
        self.assertEqual(self.rows("SELECT row_number,raw_text,parse_status FROM source_records ORDER BY row_number")[1], (2,'invalid','error'))
        p = self.root / 'multi.csv'
        with p.open('w',encoding='utf-8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['博主昵称','粉丝数']); w.writeheader(); w.writerow({'博主昵称':'多行\n名称','粉丝数':'--'}); w.writerow({'博主昵称':'乙','粉丝数':'0'})
        import_file(self.db,p,'csv')
        self.assertEqual(self.rows("SELECT row_number FROM source_records r JOIN sources s USING(source_id) WHERE s.source_name='csv' ORDER BY row_number"), [(2,), (4,)])
        self.assertEqual(self.rows("SELECT e.parse_status FROM field_evidence e JOIN source_records r USING(record_id) JOIN sources s USING(source_id) WHERE s.source_name='csv' ORDER BY r.row_number"), [('displayed_unavailable',),('parsed',)])

    def test_text_sample_only_yields_bounded_candidates_and_raw(self):
        p = self.root / 'note.txt'
        original = '标题: 示例标题\nURL: https://example.invalid/?token=private\n关注\n示例标题\n正文\n#主题\n共 1 条评论\n评论文本'
        p.write_text(original,encoding='utf-8')
        import_file(self.db,p,'note')
        self.assertEqual(self.rows('SELECT raw_text FROM source_records')[0][0], p.read_bytes().decode('utf-8'))
        values = dict(self.rows('SELECT target_field,text_value FROM field_evidence'))
        self.assertIn('正文',values['note_body_candidate'])
        self.assertNotIn('评论文本',values['note_body_candidate'])
        self.assertIn('主题',values['note_topics_candidate'])
        self.assertNotIn('note_body',values)

    def test_reject_existing_other_database_and_backup_restore(self):
        other=self.root/'other.sqlite3'
        with closing(sqlite3.connect(other)) as c: c.execute('create table important(x)'); c.commit()
        p=self.make_json('one.json',{'博主昵称':'甲'})
        with self.assertRaisesRegex(ValueError,'拒绝写入'): import_file(other,p,'bad')
        import_file(self.db,p,'good')
        backup=self.root/'backup.sqlite3'
        with closing(sqlite3.connect(self.db)) as src, closing(sqlite3.connect(backup)) as dst: src.backup(dst)
        with closing(sqlite3.connect(backup)) as c: self.assertEqual(c.execute('pragma integrity_check').fetchone()[0],'ok')
        self.assertEqual(quality_report(backup)['sources'][0]['record_count'],1)

    def test_index_only_does_not_create_identities(self):
        p=self.make_json('index.json',[{'creator_name':'甲','xhs_profile_id':'p123'}])
        import_file(self.db,p,'index',identity_index_only=True)
        self.assertEqual(self.rows('SELECT COUNT(*) FROM identities')[0][0],0)
        self.assertEqual(self.rows('SELECT identity_status FROM source_records')[0][0],'index_only')
        self.assertEqual(self.rows('SELECT COUNT(*) FROM identity_claims')[0][0],1)
        p=self.make_json('creator.json',[{'博主昵称':'甲','xhs_profile_id':'p123'}])
        import_file(self.db,p,'creator')
        self.assertEqual(quality_report(self.db)['index_hint_records'],1)


if __name__ == '__main__':
    unittest.main()
