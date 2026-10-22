"""PhysiCell settings XML and rules CSV, on files copied from PhysiCell 1.14.2 (no compiler needed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from abmfact.engines.physicell import RuleRow, SettingsError, SettingsXML, read_rules_csv, write_rules_csv
from abmfact.engines.physicell.rules import parse_rules

FIX = Path(__file__).parent / "fixtures" / "physicell"
TEMPLATE = FIX / "template" / "config" / "PhysiCell_settings.xml"
RULES_SAMPLE = FIX / "rules_sample" / "config"


def test_template_values_match_physicell_1_14_2():
    s = SettingsXML.read(TEMPLATE)
    assert s.time_steps() == {"dt_diffusion": 0.01, "dt_mechanics": 0.1, "dt_phenotype": 6.0}
    assert s.voxel() == {"dx": 20.0, "dy": 20.0, "dz": 20.0} and s.is_2d
    assert s.get_float("overall/max_time") == 7200 and s.get("parallel/omp_num_threads") == "6"
    assert s.seed_paths() == ["options/random_seed"]
    assert [v.get("name") for v in s.substrates()] == ["substrate"]
    assert [c.get("name") for c in s.cell_definitions()] == ["default"]
    assert s.user_parameters() == {"number_of_cells": ("int", "5")}
    assert s.ruleset() == (False, Path("config/cell_rules.csv"))
    assert s.cell_positions() == (False, Path("config/cells.csv"))


def test_edits_round_trip_and_keep_untouched_elements(tmp_path):
    s = SettingsXML.read(TEMPLATE)
    assert s.set_seed(42) == ["options/random_seed"]
    s.scale_time_steps(0.5)
    s.scale_voxels(0.5)
    s.set_threads(1)
    s.set_output("out", interval=30, svg=False)
    s.set_param("user_parameters.number_of_cells", 9)
    s.set_param("cell_definitions/cell_definition[@name='default']/phenotype/motility/speed", 2.5)
    again = SettingsXML.read(s.write(tmp_path / "s.xml"))
    assert again.get("options/random_seed") == "42"
    assert again.time_steps() == {"dt_diffusion": 0.005, "dt_mechanics": 0.05, "dt_phenotype": 3.0}
    assert again.voxel() == {"dx": 10.0, "dy": 10.0, "dz": 20.0}  # 2D: dz is the slab thickness
    assert again.get("save/folder") == "out" and again.get("save/SVG/enable") == "false"
    assert again.get("save/full_data/interval") == "30"
    assert again.user_parameters()["number_of_cells"] == ("int", "9")
    assert again.get("cell_definitions/cell_definition/phenotype/motility/speed") == "2.5"
    assert again.get("microenvironment_setup/variable/physical_parameter_set/decay_rate") == "10"


def test_seed_in_user_parameters_is_set_too(tmp_path):
    text = TEMPLATE.read_text().replace(
        "<user_parameters>", '<user_parameters>\n        <random_seed type="int" units="dimensionless">0</random_seed>'
    )
    text = text.replace("        <random_seed>0</random_seed>\n", "")
    path = tmp_path / "s.xml"
    path.write_text(text)
    s = SettingsXML.read(path)
    assert s.seed_paths() == ["user_parameters/random_seed"]
    assert s.set_seed(7) == ["options/random_seed", "user_parameters/random_seed"]
    assert s.get("options/random_seed") == "7" and s.get("user_parameters/random_seed") == "7"


def test_3d_scales_dz_and_comments_survive(tmp_path):
    path = tmp_path / "s.xml"
    path.write_text(TEMPLATE.read_text().replace("<use_2D>true</use_2D>", "<use_2D>false</use_2D><!-- 3D -->"))
    s = SettingsXML.read(path)
    s.scale_voxels(0.5)
    assert s.voxel() == {"dx": 10.0, "dy": 10.0, "dz": 10.0}
    assert "<!-- 3D -->" in s.write(tmp_path / "o.xml").read_text()


def test_errors(tmp_path):
    s = SettingsXML.read(TEMPLATE)
    with pytest.raises(SettingsError, match="missing"):
        s.set("overall/nope", 1)
    with pytest.raises(SettingsError, match="user parameter"):
        s.set_param("user_parameters.nope", 1)
    with pytest.raises(SettingsError, match="missing"):
        s.get_float("nope")
    s.set("overall/time_units", "min")
    with pytest.raises(SettingsError, match="not a number"):
        s.get_float("overall/time_units")
    with pytest.raises(SettingsError, match="predicate"):
        s.set("a/b[@x='1']", 1, create=True)
    bad = tmp_path / "bad.xml"
    bad.write_text("<x>")
    with pytest.raises(SettingsError, match="not valid XML"):
        SettingsXML.read(bad)
    bad.write_text("<other/>")
    with pytest.raises(SettingsError, match="expected"):
        SettingsXML.read(bad)
    assert SettingsXML.read(TEMPLATE).get("overall/nope", "x") == "x"


def test_rules_sample_csv_parses_as_physicell_does(tmp_path):
    rows, skipped = read_rules_csv(RULES_SAMPLE / "cell_rules.csv")
    assert len(rows) == 27
    assert rows[0] == RuleRow("malignant epithelial cell", "oxygen", "decreases", "necrosis", 0.0, 3.75, 8.0, False)
    assert rows[3].apply_to_dead and rows[3].behaviour == "apoptotic debris secretion"
    assert all(s.reason == "comment" for s in skipped) and len(skipped) == 8
    again, _ = read_rules_csv(write_rules_csv(rows, tmp_path / "r.csv", comment="written by a test"))
    assert again == rows
    assert (tmp_path / "r.csv").read_text().startswith("// written by a test\n")


def test_malformed_rule_lines_are_skipped_with_reasons():
    rows, skipped = parse_rules(
        "a,b,increases,c,1,2\na,b,sideways,c,1,2,3,0\na,b,increases,c,x,2,3,0\na,,increases,c,1,2,3,0\n"
    )
    assert rows == []
    assert [s.line for s in skipped] == [1, 2, 3, 4]
    assert "sideways" in skipped[1].reason and "not a number" in skipped[2].reason
    assert RuleRow("a", "b", "increases", "c", 0.5, 2.0, 4.0, True).to_fields()[4:] == ["0.5", "2", "4", "1"]
