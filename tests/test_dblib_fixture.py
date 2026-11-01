# SPDX-FileCopyrightText: 2026 nomatin
# SPDX-License-Identifier: GPL-3.0-only
import unittest

import dblib_fixture as fx


MOSFET = """* MOSFET DC test
V1 VDD 0 DC 5
R1 VDD DRAIN 1k
V2 GATE 0 DC 3
{{DUT}}
.OP
.END
"""


class ParserTests(unittest.TestCase):
    def test_mosfet_fixture(self):
        plan = fx.parse_spice_fixture(MOSFET)
        self.assertEqual(plan.dut_marker, 1)
        self.assertEqual(plan.nodes, {"VDD", "GND", "DRAIN", "GATE"})
        self.assertEqual([e.name for e in plan.elements], ["V1", "R1", "V2"])
        self.assertEqual(plan.elements[0].value, 5.0)
        self.assertEqual(plan.elements[1].value, 1000.0)
        self.assertIn("op", plan.analyses)
        self.assertTrue(plan.has_analysis)

    def test_ac_source_and_transient(self):
        text = """V1 in 0 DC 0 AC 1 90
R1 in out 1k
C1 out 0 1u
{{DUT}}
.TRAN 1u 1m
.END
"""
        plan = fx.parse_spice_fixture(text)
        self.assertEqual(plan.elements[0].ac_magnitude, 1.0)
        self.assertEqual(plan.elements[0].ac_phase, 90.0)
        self.assertEqual(plan.analyses["tran"]["step"], 1e-6)
        updates = fx.sim_config_updates(plan)
        self.assertEqual(updates["TRAN_Analysis"], "True")
        text = """V1 in 0 DC 0 AC 1
R1 in out 1k
{{DUT}}
.AC DEC 20 1 1MEG
.END
"""
        plan = fx.parse_spice_fixture(text)
        self.assertEqual(plan.analyses["ac"]["sweep"], "DEC")
        updates = fx.sim_config_updates(plan)
        self.assertEqual(updates["AC_Analysis"], "True")
        self.assertEqual(updates["AC_StopFrequency"], "1000000")

    def test_multiple_analyses_rejected(self):
        text = "V1 a 0 DC 1\n{{DUT}}\n.OP\n.TRAN 1u 1m\n.END\n"
        with self.assertRaises(fx.FixtureError) as ctx:
            fx.parse_spice_fixture(text)
        self.assertEqual(ctx.exception.code, "FIXTURE_ANALYSIS_MULTIPLE")

    def test_pin_map(self):
        plan = fx.parse_spice_fixture(MOSFET)
        pins = fx.validate_pin_map({"1": "GATE", "2": "GND", "3": "DRAIN"},
                                   plan)
        self.assertEqual(pins, {"1": "GATE", "2": "GND", "3": "DRAIN"})
        self.assertIn("1|GATE", fx.pin_map_lines(pins))

    def test_unconnected_pin(self):
        plan = fx.parse_spice_fixture(MOSFET)
        pins = fx.validate_pin_map({"1": "GATE", "2": None, "3": "DRAIN"}, plan)
        self.assertIsNone(pins["2"])
        self.assertIn("2|<NULL>", fx.pin_map_lines(pins))

    def test_unknown_net_rejected(self):
        plan = fx.parse_spice_fixture(MOSFET)
        with self.assertRaises(fx.FixtureError) as ctx:
            fx.validate_pin_map({"1": "GATE", "2": "NOT_IN_FIXTURE"}, plan)
        self.assertEqual(ctx.exception.code, "COMPONENT_PIN_NET_UNKNOWN")

    def test_unsupported_element(self):
        text = "Q1 c b e MODEL\n{{DUT}}\n.OP\n.END\n"
        with self.assertRaises(fx.FixtureError) as ctx:
            fx.parse_spice_fixture(text)
        self.assertEqual(ctx.exception.code, "FIXTURE_ELEMENT_UNSUPPORTED")

    def test_unsupported_directive(self):
        text = "V1 a 0 DC 1\n{{DUT}}\n.TEMP 25\n.OP\n.END\n"
        with self.assertRaises(fx.FixtureError) as ctx:
            fx.parse_spice_fixture(text)
        self.assertEqual(ctx.exception.code, "FIXTURE_DIRECTIVE_UNSUPPORTED")

    def test_dut_marker_required_once(self):
        for text in ("V1 a 0 DC 1\n.OP\n.END\n",
                     "V1 a 0 DC 1\n{{DUT}}\n{{DUT}}\n.OP\n.END\n"):
            with self.subTest(text=text), self.assertRaises(fx.FixtureError) as ctx:
                fx.parse_spice_fixture(text)
            self.assertEqual(ctx.exception.code, "FIXTURE_DUT_MARKER")

    def test_values(self):
        self.assertEqual(fx.parse_number("1k"), 1000.0)
        self.assertEqual(fx.parse_number("1MEG"), 1e6)
        self.assertEqual(fx.parse_number("1m"), 1e-3)
        self.assertEqual(fx.parse_number("1u"), 1e-6)
        self.assertEqual(fx.parse_number("1e-6"), 1e-6)
        with self.assertRaises(fx.FixtureError):
            fx.parse_number("{R}")

    def test_options_subset(self):
        text = "V1 a 0 DC 1\n{{DUT}}\n.OP\n.OPTIONS METHOD=GEAR DVCC=5\n.END\n"
        plan = fx.parse_spice_fixture(text)
        updates = fx.sim_config_updates(plan)
        self.assertEqual(updates["OPTION_Method"], "GEAR")
        self.assertEqual(updates["OPTION_DVCC"], "5")
        text = "V1 a 0 DC 1\n{{DUT}}\n.OP\n.OPTIONS RELTOL=1m\n.END\n"
        with self.assertRaises(fx.FixtureError) as ctx:
            fx.parse_spice_fixture(text)
        self.assertEqual(ctx.exception.code, "FIXTURE_OPTIONS_UNSUPPORTED")


if __name__ == "__main__":
    unittest.main()
