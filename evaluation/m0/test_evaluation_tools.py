#!/usr/bin/env python3
"""Regression tests for M0 evaluation-tool boundary semantics."""

from __future__ import annotations

import unittest

from run_retrieval_baseline import rank_metrics, summarize


class RetrievalMetricTests(unittest.TestCase):
    def test_empty_ranked_results_are_zero_not_an_index_error(self) -> None:
        self.assertEqual(
            rank_metrics({"log.csv:000001"}, []),
            {
                "recall_at_1": 0.0,
                "recall_at_5": 0.0,
                "recall_at_10": 0.0,
                "mrr_at_10": 0.0,
                "ndcg_at_10": 0.0,
            },
        )

    def test_no_positive_cases_fail_with_actionable_error(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one positive case"):
            summarize([{"is_negative": True}])

    def test_relevant_empty_cases_do_not_contribute_metrics(self) -> None:
        self.assertEqual(rank_metrics(set(), []), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
