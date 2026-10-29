import unittest

from tools import merge_paper_lists, papers_match


class PaperMergeTests(unittest.TestCase):
    def test_merges_arxiv_and_google_scholar_versions_by_title(self):
        gs_paper = {
            "title": "TacSE3:EquivariantSE (3) Motion Estimation from Low-Texture Visuotactile Images for In-Gripper Tracking and Compensation",
            "source": "google_scholar",
            "paper_id": "gs_-4504773545519295721",
            "url": "https://arxiv.org/abs/2605.17929",
            "citations": 12,
        }
        arxiv_paper = {
            "title": "TacSE3: Equivariant SE(3) Motion Estimation from Low-Texture Visuotactile Images for In-Gripper Tracking and Compensation",
            "source": "arxiv",
            "paper_id": "2605.17929v2",
            "url": "http://arxiv.org/abs/2605.17929v2",
            "pdf_url": "https://arxiv.org/pdf/2605.17929v2",
        }

        merged = merge_paper_lists([], [gs_paper, arxiv_paper])

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["paper_id"], "2605.17929v2")
        self.assertEqual(merged[0]["pdf_url"], "https://arxiv.org/pdf/2605.17929v2")
        self.assertEqual(merged[0]["citations"], 12)
        self.assertEqual(merged[0]["source"], "arxiv+google_scholar")
        self.assertEqual(merged[0]["title"], arxiv_paper["title"])

    def test_matches_titles_with_missing_spaces(self):
        self.assertTrue(
            papers_match(
                {"title": "Residual Rotation Correction using Tactile Equivariance"},
                {"title": "Residual rotation correction usingtactile equivariance"},
            )
        )


if __name__ == "__main__":
    unittest.main()
