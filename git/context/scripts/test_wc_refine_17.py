"""Offline tests for the Sprint 17 schedule rules (48-hour checkpoint, early projection). No model calls."""
import unittest

import wc_refine_17 as w

PLAN = {"order": ["A1", "A2", "A3", "A4"], "area_pages": {"A1": 10, "A2": 20, "A3": 30, "A4": 40}}


def end(arm, kind, area, seconds, t, action="advance", resume_due=False):
    return {"event": "end", "arm": arm, "kind": kind, "area": area, "seconds": seconds, "tokens": 100, "time": t,
            "action": action, "resume_due": resume_due, "head_after": f"{arm}{area}", "head_after_path": "x"}


def launch(arm, kind, area, t):
    return {"event": "launch", "arm": arm, "kind": kind, "area": area, "time": t, "namespace": "n"}


class ScheduleTests(unittest.TestCase):
    def test_checkpoint_only_after_48h_from_first_edit_launch(self):
        rows = [end("WS", "survey", "ALL", 700, 0), launch("LT", "pass", "A1", 1000)]
        self.assertFalse(w.checkpoint_due([end("WS", "survey", "ALL", 700, 0)], 10 ** 9, None))
        self.assertFalse(w.checkpoint_due(rows, 1000 + 48 * 3600 - 1, None))
        self.assertTrue(w.checkpoint_due(rows, 1000 + 48 * 3600, None))
        self.assertTrue(w.checkpoint_due(rows, 1000 + 48 * 3600, {"decision": "stop"}))
        self.assertFalse(w.checkpoint_due(rows, 1000 + 48 * 3600, {"decision": "continue"}))

    def test_area_done_needs_both_arms_and_no_pending_resume(self):
        rows = [end("LT", "pass", "A1", 60, 1)]
        self.assertFalse(w.area_done(rows, "A1"))
        rows.append(end("R0", "pass", "A1", 60, 2, resume_due=True))
        self.assertFalse(w.area_done(rows, "A1"))
        rows.append(end("R0", "resume", "A1", 30, 3))
        self.assertTrue(w.area_done(rows, "A1"))

    def test_projection_scales_by_remaining_pages(self):
        rows = []
        for a, t in (("A1", 1), ("A2", 2), ("A3", 3)):
            rows += [end("LT", "pass", a, 600, t), end("R0", "pass", a, 300, t)]
        p = w.projection(rows, PLAN)
        self.assertEqual(p["basis_pages"], 60)
        self.assertEqual(p["remaining_areas"], 1)
        self.assertEqual(p["per_arm"]["LT"]["seconds_per_page"], 30.0)
        self.assertEqual(p["per_arm"]["LT"]["remaining_area_seconds"], 1200)
        self.assertEqual(p["per_arm"]["R0"]["remaining_area_seconds"], 600)
        self.assertIsNone(w.projection(rows[:-1], PLAN))

    def test_void_rows_drop_out_of_the_replayed_state(self):
        rows = [dict(end("WS", "survey", "ALL", 700, 0), seq=1), dict(launch("LT", "pass", "A1", 1), seq=2),
                dict(end("LT", "pass", "A1", 20, 2, action="rollback"), seq=3),
                {"event": "halt", "reason": "infra", "seq": 4}, {"event": "void", "seqs": [2, 3, 4], "seq": 5}]
        st = w.wr.ledger_state(rows)
        self.assertIsNone(st["halted"])
        self.assertNotIn(("LT", "pass", "A1"), st["done"])
        self.assertEqual(st["arms"]["LT"]["tokens"], 100)  # the survey row only
        self.assertIsNone(w.first_edit_launch(rows))
        self.assertEqual(w.wr.next_item(rows, w.wr.planned_items(["A1"]))[0]["area"], "A1")

    def test_amend_rows_patch_the_end_row(self):
        rows = [dict(launch("R0", "pass", "A1", 1), seq=1),
                dict(end("R0", "pass", "A1", 20, 2, action="rollback"), seq=2, head_after="base", head_after_path=None),
                {"event": "amend", "amends_seq": 2, "fields": {"action": "advance", "head_after": "t1",
                                                               "head_after_path": "p1"}, "seq": 3}]
        st = w.wr.ledger_state(rows)
        self.assertEqual(st["arms"]["R0"]["head"], "t1")
        self.assertEqual(st["arms"]["R0"]["sessions"], 1)

    def test_hugo_target_maps_site_urls_to_pages(self):
        rels = {"content/manuals/dhi/how-to/scan.md", "content/guides/x/_index.md", "content/manuals/dhi/_index.md"}
        self.assertEqual(w.hugo_target("/dhi/how-to/scan/", rels), "content/manuals/dhi/how-to/scan.md")
        self.assertEqual(w.hugo_target("/dhi/#top", rels), "content/manuals/dhi/_index.md")
        self.assertEqual(w.hugo_target("/guides/x/", rels), "content/guides/x/_index.md")
        self.assertIsNone(w.hugo_target("/dhi/how-to/kubernetes/", rels))
        self.assertIsNone(w.hugo_target("../scan/", rels))
        self.assertIsNone(w.hugo_target("/", rels))

    def test_prompt_marker_does_not_count_as_a_lost_line(self):
        base = ["$ docker extension update a:latest\n", "docker extension install a:latest\n", "$ docker rm x\n"]
        var = ["docker extension update a:latest\n$ docker extension install a:latest\n"]
        self.assertEqual(w.sfb.protected_missing_v3(base, var), ["$ docker rm x"])
        self.assertEqual(w.sfb.protected_missing_v3(['$ openssl x509 -req  -days 3650  -in "a.csr"\n'],
                                                    ['$ openssl x509 -req -days 3650 -in "a.csr"\n']), [])

    def test_reopen_reruns_the_item_and_keeps_its_cost(self):
        rows = [dict(launch("LT", "pass", "A1", 1), seq=1),
                dict(end("LT", "pass", "A1", 20, 2, action="rollback"), seq=2),
                {"event": "reopen", "reopens_seq": 2, "arm": "LT", "kind": "pass", "area": "A1", "seq": 3}]
        st = w.wr.ledger_state(rows)
        self.assertNotIn(("LT", "pass", "A1"), st["done"])
        self.assertEqual(st["arms"]["LT"]["tokens"], 100)
        self.assertEqual(w.wr.next_item(rows, w.wr.planned_items(["A1"]))[0], {"arm": "LT", "kind": "pass", "area": "A1"})

    def test_c1b_exemption_needs_a_real_template_reference(self):
        rels = {"content/includes/a/b.md", "content/x/_index.md", "content/x/child.md", "content/y/_index.md"}
        used = ['{{% include "a/b.md" %}}']
        self.assertTrue(w.templated_exempt("content/includes/a/b.md", rels, used))
        self.assertFalse(w.templated_exempt("content/includes/a/b.md", rels, ["no shortcode here"]))
        self.assertTrue(w.templated_exempt("content/x/_index.md", rels, []))
        self.assertFalse(w.templated_exempt("content/y/_index.md", rels, []))
        self.assertFalse(w.templated_exempt("content/z/page.md", rels, used))

    def test_closing_an_unclosed_fence_attribute_is_not_a_loss(self):
        base = ['```json {title="Example configuration"\n{}\n```\n']
        self.assertEqual(w.sfb.protected_missing_v3(base, ['```json {title="Example configuration"}\n{}\n```\n']), [])
        self.assertEqual(w.sfb.protected_missing_v3(base, ['```json\n{}\n```\n']),
                         ['```json {title="Example configuration"'])


if __name__ == "__main__":
    unittest.main()
