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


if __name__ == "__main__":
    unittest.main()
