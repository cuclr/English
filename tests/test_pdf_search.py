import unittest

from pdf_search import PdfSearcher, find_vocabulary_pdf


class PdfSearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pdf_path = find_vocabulary_pdf(".")
        cls.searcher = PdfSearcher(pdf_path)

    def test_entry_continues_onto_next_page(self):
        entry = self.searcher.search("demonstrate")

        self.assertIsNotNone(entry)
        self.assertIn("说明", entry.definition)
        self.assertGreaterEqual(len(entry.phrases), 4)
        self.assertTrue(
            any("向某人说明某事" in phrase for phrase in entry.phrases)
        )

    def test_heading_with_ocr_separator_is_found(self):
        entry = self.searcher.search("atmosphere")

        self.assertIsNotNone(entry)
        self.assertIn("大气", entry.definition)
        self.assertNotIn("atmosphe_re", entry.definition)
        self.assertTrue(any("大气污染" in phrase for phrase in entry.phrases))

    def test_heading_with_ocr_missing_letter_marker_is_found(self):
        entry = self.searcher.search("scholar")

        self.assertIsNotNone(entry)
        self.assertIn("学者", entry.definition)

    def test_heading_with_ocr_wrong_letter_is_found(self):
        entry = self.searcher.search("inspire")

        self.assertIsNotNone(entry)
        self.assertIn("鼓舞", entry.definition)

    def test_small_text_fragment_does_not_end_catalog_entry(self):
        entry = self.searcher.search("catalog")

        self.assertIsNotNone(entry)
        self.assertIn("目录", entry.definition)
        self.assertTrue(any("音乐会目录" in phrase for phrase in entry.phrases))

    def test_heading_with_extra_ocr_symbol_is_found(self):
        entry = self.searcher.search("category")

        self.assertIsNotNone(entry)
        self.assertIn("类别", entry.definition)

    def test_ocr_heading_match_stays_limited(self):
        matches = {
            "atmosphere": "atmosphe_re",
            "transport": "tran~ort",
            "objective": "obje:ctive",
            "conquer": "con_guer",
            "beloved": "beloy_~d",
        }
        for query, heading in matches.items():
            with self.subTest(query=query):
                self.assertTrue(
                    self.searcher._heading_matches_query(
                        heading, query, allow_fuzzy=True
                    )
                )

        self.assertFalse(self.searcher._heading_matches_query("plain", "plane"))

    def test_fuzzy_heading_requires_footer_confirmation(self):
        self.assertFalse(self.searcher._heading_matches_query("g~us_", "use"))
        self.assertTrue(
            self.searcher._heading_matches_query(
                "insqir::__e", "inspire", allow_fuzzy=True
            )
        )

    def test_footer_words_are_not_fuzzy_matched_to_a_shorter_word(self):
        entry = self.searcher.search("use")

        self.assertIsNone(entry)

    def test_split_heading_fragments_are_joined(self):
        entry = self.searcher.search("junior")

        self.assertIsNotNone(entry)
        self.assertIn("初级", entry.definition)
        self.assertNotIn("jun ior", entry.definition.lower())
        self.assertTrue(any("初级研究者" in phrase for phrase in entry.phrases))

    def test_secondary_phonetic_is_removed_but_grammar_label_is_kept(self):
        entry = self.searcher.search("decrease")

        self.assertIsNotNone(entry)
        self.assertIn("减少", entry.definition)
        self.assertIn("[C, U]", entry.definition)
        self.assertNotIn("kri", entry.definition.lower())
        self.assertNotIn("中．", entry.definition)

    def test_definition_cleanup_handles_split_secondary_phonetic(self):
        words = [
            (60.0, 100.0, 100.0, 114.0, "decrease", 1, 0, 0),
            (112.0, 101.0, 145.0, 113.0, "[dɪ'kri:s]", 1, 1, 0),
            (148.0, 101.0, 154.0, 113.0, "v.", 1, 1, 1),
            (157.0, 101.0, 195.0, 113.0, "减少［中．kri", 1, 1, 2),
            (196.0, 101.0, 208.0, 113.0, ":s]", 1, 1, 3),
            (211.0, 101.0, 221.0, 113.0, "n.", 1, 1, 4),
            (224.0, 101.0, 244.0, 113.0, "[C,", 1, 1, 5),
            (247.0, 101.0, 260.0, 113.0, "U]", 1, 1, 6),
            (263.0, 101.0, 285.0, 113.0, "减少", 1, 1, 7),
        ]

        definition = self.searcher._extract_definition(words, 100.0, 100.0)

        self.assertEqual(definition, "v. 减少 n. [C, U] 减少")


if __name__ == "__main__":
    unittest.main()
