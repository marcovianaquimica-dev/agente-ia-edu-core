import unittest
import uuid

from agente_ia_edu.services.mass_correction_sampling import select_sample_for_review


class SelectSampleForReviewTests(unittest.TestCase):
    def test_a_correction_with_alerts_always_goes_to_the_sample(self):
        correction_id = uuid.uuid4()
        sample_ids, auto_approve_ids = select_sample_for_review(
            [{"id": correction_id, "alerts": ["FUGA_AO_TEMA"], "has_scores": True}],
            sample_rate=0.0,  # mesmo com taxa zero, alerta sempre entra
        )
        self.assertEqual(sample_ids, [correction_id])
        self.assertEqual(auto_approve_ids, [])

    def test_a_correction_without_scores_always_goes_to_the_sample(self):
        correction_id = uuid.uuid4()
        sample_ids, auto_approve_ids = select_sample_for_review(
            [{"id": correction_id, "alerts": [], "has_scores": False}],
            sample_rate=0.0,
        )
        self.assertEqual(sample_ids, [correction_id])

    def test_sample_rate_100_percent_puts_everything_in_the_sample(self):
        ids = [uuid.uuid4() for _ in range(20)]
        corrections = [{"id": i, "alerts": [], "has_scores": True} for i in ids]
        sample_ids, auto_approve_ids = select_sample_for_review(corrections, sample_rate=1.0)
        self.assertEqual(set(sample_ids), set(ids))
        self.assertEqual(auto_approve_ids, [])

    def test_sample_rate_0_percent_auto_approves_everything_healthy(self):
        ids = [uuid.uuid4() for _ in range(20)]
        corrections = [{"id": i, "alerts": [], "has_scores": True} for i in ids]
        sample_ids, auto_approve_ids = select_sample_for_review(corrections, sample_rate=0.0)
        self.assertEqual(sample_ids, [])
        self.assertEqual(set(auto_approve_ids), set(ids))

    def test_selection_is_deterministic_across_calls(self):
        ids = [uuid.uuid4() for _ in range(50)]
        corrections = [{"id": i, "alerts": [], "has_scores": True} for i in ids]
        first_call = select_sample_for_review(corrections, sample_rate=0.2)
        second_call = select_sample_for_review(corrections, sample_rate=0.2)
        self.assertEqual(first_call, second_call)


if __name__ == "__main__":
    unittest.main()
