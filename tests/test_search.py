import unittest
from unittest.mock import patch, MagicMock
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
from search import search, format_search_context, SearchResult
import urllib.error

class TestSearch(unittest.TestCase):
    def test_search_returns_results(self):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "results": [
                {"url": "http://example.com/1", "title": "Result 1", "content": "Snippet 1"},
                {"url": "http://example.com/2", "title": "Result 2", "content": "Snippet 2"}
            ]
        }).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch('search.urlopen', return_value=mock_response):
            results = search("test query", max_results=2)
            self.assertEqual(len(results), 2)
            self.assertIsInstance(results[0], SearchResult)
            self.assertEqual(results[0].url, "http://example.com/1")
            self.assertEqual(results[0].title, "Result 1")
            self.assertEqual(results[0].snippet, "Snippet 1")

    def test_search_empty_query(self):
        results = search("")
        self.assertEqual(results, [])

    def test_search_network_failure(self):
        with patch('search.urlopen', side_effect=urllib.error.URLError("timeout")):
            results = search("test")
            self.assertEqual(results, [])

    def test_search_json_decode_error(self):
        mock_response = MagicMock()
        mock_response.read.return_value = b"invalid json"
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch('search.urlopen', return_value=mock_response):
            results = search("test")
            self.assertEqual(results, [])

    def test_search_no_results(self):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({"results": []}).encode()
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)
        with patch('search.urlopen', return_value=mock_response):
            results = search("test")
            self.assertEqual(results, [])

    def test_format_search_context(self):
        results = [
            SearchResult("Title A", "Snippet A", "http://a.com"),
            SearchResult("Title B", "Snippet B", "http://b.com")
        ]
        formatted = format_search_context(results)
        self.assertIn("[Web search results]", formatted)
        self.assertIn("1. Title A", formatted)
        self.assertIn("   Snippet A", formatted)
        self.assertIn("   Source: http://a.com", formatted)
        self.assertIn("2. Title B", formatted)
        self.assertIn("   Snippet B", formatted)
        self.assertIn("   Source: http://b.com", formatted)
        self.assertIn("[End search results]", formatted)

    def test_format_search_context_empty(self):
        self.assertEqual(format_search_context([]), "")

if __name__ == "__main__":
    unittest.main()