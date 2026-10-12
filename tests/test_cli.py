import csv

import pytest

from refi_radar.cli import main


def test_generate_then_analyze_writes_briefing_and_call_list(tmp_path, capsys):
    tape = tmp_path / "tape.csv"
    out = tmp_path / "reports"
    assert main(["generate", "--count", "40", "--seed", "3", "--out", str(tape)]) == 0
    assert main(["analyze", "--tape", str(tape), "--scenario", "severe", "--top", "5", "--out", str(out)]) == 0

    output = capsys.readouterr().out
    assert "# Refi Radar briefing: Severe Downturn" in output
    assert "555-01" in output  # contacts were joined from the generated CRM file
    assert (out / "briefing_severe.md").exists()
    with (out / "call_list_severe.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert rows and rows[0]["rank"] == "1"
    assert rows[0]["contact_phone"].startswith("(212) 555-01")
    assert rows[0]["next_step"].startswith("Pitch")
    assert (tmp_path / "crm_contacts.csv").exists()
    assert float(rows[0]["priority_score"]) >= float(rows[-1]["priority_score"])


def test_analyze_with_overrides_names_scenario_custom(tmp_path, capsys):
    tape = tmp_path / "tape.csv"
    main(["generate", "--count", "30", "--out", str(tape)])
    assert main(["analyze", "--tape", str(tape), "--rate-shock-bps", "150"]) == 0
    assert "briefing: Custom Scenario" in capsys.readouterr().out


def test_missing_tape_is_a_clean_error(tmp_path, capsys):
    assert main(["analyze", "--tape", str(tmp_path / "nope.csv")]) == 2
    assert "error" in capsys.readouterr().err


def test_unknown_scenario_is_rejected_by_argparse():
    with pytest.raises(SystemExit):
        main(["analyze", "--scenario", "apocalypse"])


def test_analyze_without_contacts_file_still_works(tmp_path, capsys):
    tape = tmp_path / "tape.csv"
    main(["generate", "--count", "20", "--out", str(tape)])
    (tmp_path / "crm_contacts.csv").unlink()
    assert main(["analyze", "--tape", str(tape), "--top", "3"]) == 0
    assert "555-01" not in capsys.readouterr().out
