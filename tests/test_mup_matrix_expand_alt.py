"""tests/test_mup_matrix_expand_alt.py

Unit and regression test suite for mup_matrix_expand_alt.py.
Validates:
1. Pure-Python WRP binary parser (100-byte file header, 64-byte block headers).
2. DataSense metadata extraction (X, Y, Z, H, Mean wind speed, Turbine filtering).
3. Analytical mathematical expander (Weibull CDF integration, 12->16 circular interpolation).
4. Frandsen wake turbulence formulations (Fast & Complete Frandsen).
5. IEC 61400-1 Annex D Effective Turbulence calculation.
6. Grid-by-grid reconciliation against Meteodyn Universe GUI ground truth.
"""

import math
import os
import sys
import unittest
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mup_matrix_expand_alt import (
    DataSense,
    DIRECTIONS_16,
    SECTORS_12,
    SPEED_BINS_59,
    MathematicalExpander,
    MatrixReconciler,
    WriAsciiReader,
    WrpBinaryReader,
    expand_matrix,
)

# Reference test fixtures
SYNTHESIS_WRI = Path(
    r"H:\ProgramData\Meteodyn\MUP\local_liao_be917e43-a15c-4db7-8573-822cecb16f78\SYNTHESIS_FILES\ea368c7f-9810-487d-be4c-bbcc9339c83e_synthesis_2.wri"
)
SYNTHESIS_WRP = Path(
    r"H:\ProgramData\Meteodyn\MUP\local_liao_be917e43-a15c-4db7-8573-822cecb16f78\SYNTHESIS_FILES\ea368c7f-9810-487d-be4c-bbcc9339c83e_synthesis_2.wrp"
)
GUI_REF_FREQ = Path(
    r"E:\My_RF_Project\MicroSitingDecisionAnalyst\data\demo_wt\DEMO (1)_WRA2_Frequency_Matrix.txt"
)
GUI_REF_TURB = Path(
    r"E:\My_RF_Project\MicroSitingDecisionAnalyst\data\demo_wt\DEMO (1)_WRA2_TurbulenceIntensityTotal_Matrix.txt"
)


class TestMathematicalExpander(unittest.TestCase):
    """Tests the analytical formulation in MathematicalExpander."""

    def test_weibull_cdf(self):
        # A=8.0, k=2.0 (Rayleigh) -> F(A) = 1 - exp(-1) = 0.63212
        val = MathematicalExpander.weibull_cdf(8.0, 8.0, 2.0)
        self.assertAlmostEqual(val, 1.0 - math.exp(-1.0), places=5)
        # Boundary cases
        self.assertEqual(MathematicalExpander.weibull_cdf(0.0, 8.0, 2.0), 0.0)
        self.assertEqual(MathematicalExpander.weibull_cdf(-5.0, 8.0, 2.0), 0.0)

    def test_weibull_bin_probability(self):
        # Bin 8 (7.5 to 8.5)
        p = MathematicalExpander.weibull_bin_probability(8.0, 8.0, 2.0, bin_width=1.0)
        expected = MathematicalExpander.weibull_cdf(8.5, 8.0, 2.0) - MathematicalExpander.weibull_cdf(7.5, 8.0, 2.0)
        self.assertAlmostEqual(p, expected, places=6)
        self.assertGreater(p, 0.0)

    def test_periodic_interpolation_12_to_16(self):
        # Constant array should remain constant
        const_12 = [5.0] * 12
        interp_16 = MathematicalExpander.interpolate_periodic_12_to_16(const_12)
        self.assertEqual(len(interp_16), 16)
        for val in interp_16:
            self.assertAlmostEqual(val, 5.0, places=5)

    def test_iec_effective_turbulence(self):
        # Equal directional TI = 0.12, equal frequencies
        tis = [0.12] * 16
        freqs = [1.0 / 16.0] * 16
        eff_ti = MathematicalExpander.calculate_iec_effective_turbulence(tis, freqs, wohler_m=10.0)
        self.assertAlmostEqual(eff_ti, 0.12, places=4)

        # Single dominant direction with higher TI
        tis_dominant = [0.10] * 15 + [0.20]
        freqs_dominant = [0.0] * 15 + [1.0]
        eff_dom = MathematicalExpander.calculate_iec_effective_turbulence(tis_dominant, freqs_dominant, wohler_m=10.0)
        self.assertAlmostEqual(eff_dom, 0.20, places=4)

    def test_frandsen_wake_models(self):
        # Separation s = 500m, D = 100m (s/D = 5), Ct = 0.8
        fast = MathematicalExpander.frandsen_added_turbulence(500.0, 100.0, 0.8, complete_model=False)
        complete = MathematicalExpander.frandsen_added_turbulence(500.0, 100.0, 0.8, complete_model=True)
        self.assertGreater(fast, 0.0)
        self.assertGreater(complete, 0.0)
        # Fast model gives ~ sqrt(0.9) / (1.5 + 0.3 * 5 * sqrt(0.8)) = 0.94868 / (1.5 + 1.34164) = 0.3338
        self.assertAlmostEqual(fast, math.sqrt(0.9) / (1.5 + 0.3 * 5.0 * math.sqrt(0.8)), places=4)


class TestWriAsciiReader(unittest.TestCase):
    """Tests parsing low-resolution .wri files."""

    @unittest.skipUnless(SYNTHESIS_WRI.exists(), "Requires real synthesis_2.wri")
    def test_parse_real_wri(self):
        reader = WriAsciiReader(SYNTHESIS_WRI)
        self.assertEqual(reader.version, 2)
        self.assertEqual(reader.point_count, 6)
        self.assertIn("W1", reader.points)
        self.assertIn("W4", reader.points)
        # 13 directions x 3 speeds = 39 records per point
        self.assertEqual(len(reader.points["W1"]), 39)


class TestWrpBinaryReader(unittest.TestCase):
    """Tests the pure Python WRP parser and DataSense extraction."""

    @unittest.skipUnless(SYNTHESIS_WRP.exists(), "Requires real synthesis_2.wrp")
    def test_wrp_header_structure(self):
        reader = WrpBinaryReader(SYNTHESIS_WRP)
        self.assertEqual(reader.file_type, 1001)
        self.assertEqual(reader.version, 2)
        self.assertEqual(reader.nb_directions, 16)
        self.assertEqual(reader.nb_windspeeds, 60)
        self.assertEqual(reader.nb_points, 7)
        self.assertEqual(reader.nb_blocks, 9644)

    @unittest.skipUnless(SYNTHESIS_WRP.exists(), "Requires real synthesis_2.wrp")
    def test_wrp_turbines_and_coordinates(self):
        reader = WrpBinaryReader(SYNTHESIS_WRP)
        # 7 points total: W4, W2, W5, W1, M1, W6, W3
        labels = [t.label for t in reader.turbines]
        self.assertIn("W1", labels)
        self.assertIn("M1", labels)

        # M1 must be identified as met mast (is_turbine=False)
        m1 = [t for t in reader.turbines if t.label == "M1"][0]
        self.assertFalse(m1.is_turbine)

        # W1 coordinates must match Meteodyn GUI exactly
        w1 = [t for t in reader.turbines if t.label == "W1"][0]
        self.assertTrue(w1.is_turbine)
        self.assertAlmostEqual(w1.x, 38556424.0, delta=1.0)
        self.assertAlmostEqual(w1.y, 3163553.0, delta=1.0)
        self.assertAlmostEqual(w1.z, 1397.4, delta=0.5)
        self.assertAlmostEqual(w1.h, 90.0, delta=0.1)
        self.assertAlmostEqual(w1.mean_speed, 6.56, places=2)

    @unittest.skipUnless(SYNTHESIS_WRP.exists() and GUI_REF_FREQ.exists(), "Requires real files")
    def test_frequency_matrix_reconciliation(self):
        # Expand to temp directory
        out_dir = PROJECT_ROOT / "04-WT输出" / "test_run"
        out_dir.mkdir(parents=True, exist_ok=True)
        freq_path, _ = expand_matrix(
            wri_path=SYNTHESIS_WRI,
            wrp_path=SYNTHESIS_WRP,
            output_dir=out_dir,
            mode="wrp"
        )

        summaries = MatrixReconciler.reconcile_frequency(freq_path, GUI_REF_FREQ)
        self.assertEqual(len(summaries), 6)  # 6 turbines W1..W6
        for s in summaries:
            # 16 directions x 59 bins = 944 numerical cells
            self.assertEqual(s.cells_compared, 944)
            # Pass rate must be 100%
            self.assertEqual(s.pass_rate, 100.0)
            # Maximum absolute diff across 944 cells <= 0.01
            self.assertLessEqual(s.max_abs_diff, 0.0101)


class TestModeWriAnalyticalExpansion(unittest.TestCase):
    """Tests the standalone Mode WRI expansion when WRP is absent."""

    @unittest.skipUnless(SYNTHESIS_WRI.exists(), "Requires real synthesis_2.wri")
    def test_wri_mode_generates_valid_matrices(self):
        out_dir = PROJECT_ROOT / "04-WT输出" / "test_wri_run"
        out_dir.mkdir(parents=True, exist_ok=True)
        freq_path, turb_path = expand_matrix(
            wri_path=SYNTHESIS_WRI,
            output_dir=out_dir,
            mode="wri",
            wohler_m=10.0
        )
        self.assertTrue(freq_path.exists())
        self.assertTrue(turb_path.exists())

        # Validate frequency format
        with open(freq_path, "r", encoding="utf-8-sig") as f:
            content = f.read()
        self.assertIn("Label \t= \tW1", content)
        self.assertIn("Speed\xa0(m/s)", content)
        self.assertIn("Freq\xa0(%)", content)
        self.assertIn("59          ", content)


if __name__ == "__main__":
    unittest.main()
