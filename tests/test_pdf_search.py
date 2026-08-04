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


if __name__ == "__main__":
    unittest.main()
