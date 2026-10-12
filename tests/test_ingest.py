import pytest

from refi_radar.ingest import REQUIRED_COLUMNS, TapeFormatError, load_tape, parse_tape_text, write_tape

HEADER = ",".join(REQUIRED_COLUMNS)
GOOD = 'L-1,Test Tower,office,"Chicago, IL",Sponsor,CMBS,2019-04-01,2027-04-01,50000000,0.04,0,80000000,6000000,0.85'


def tape(*rows: str) -> str:
    return "\n".join([HEADER, *rows])


def test_parses_a_clean_row():
    result = parse_tape_text(tape(GOOD))
    assert not result.errors
    assert result.loans[0].original_balance == 50_000_000
    assert result.loans[0].market == "Chicago, IL"


def test_normalises_messy_servicer_formats():
    messy = 'L-1,Test Tower,Office,"Chicago, IL",Sponsor,CMBS,2019-04-01,2027-04-01,"$50,000,000",4.5%,0,"$80,000,000","$6,000,000",92'
    loan = parse_tape_text(tape(messy)).loans[0]
    assert loan.original_balance == 50_000_000
    assert loan.note_rate == pytest.approx(0.045)
    assert loan.occupancy == pytest.approx(0.92)
    assert loan.property_type.value == "office"


def test_full_occupancy_written_as_one_is_not_one_percent():
    row = GOOD.rsplit(",", 1)[0] + ",1"
    assert parse_tape_text(tape(row)).loans[0].occupancy == 1.0


def test_bad_rows_are_rejected_with_row_numbers_and_good_rows_survive():
    bad_type = GOOD.replace("L-1", "L-2").replace("office", "spaceship")
    bad_dates = GOOD.replace("L-1", "L-3").replace("2027-04-01", "2018-01-01")
    bad_number = GOOD.replace("L-1", "L-4").replace("50000000", "fifty million")
    result = parse_tape_text(tape(GOOD, bad_type, bad_dates, bad_number))

    assert [l.loan_id for l in result.loans] == ["L-1"]
    errors = {(e.row, e.field) for e in result.errors}
    assert errors == {(3, "property_type"), (4, "maturity_date"), (5, "original_balance")}
    assert result.data_quality() == {"rows_read": 4, "rows_accepted": 1, "rows_rejected": 3}


def test_duplicate_loan_ids_are_rejected():
    result = parse_tape_text(tape(GOOD, GOOD))
    assert len(result.loans) == 1
    assert result.errors[0].message == "duplicate loan_id"


def test_blank_required_text_is_reported():
    result = parse_tape_text(tape(GOOD.replace("Sponsor", " ")))
    assert result.errors[0].field == "sponsor"
    assert result.errors[0].message == "is blank"


def test_missing_columns_fail_the_whole_file():
    with pytest.raises(TapeFormatError, match="occupancy"):
        parse_tape_text("loan_id,property_name\nL-1,X")


def test_round_trip_preserves_loans(tmp_path, synthetic_loans):
    path = tmp_path / "tape.csv"
    write_tape(synthetic_loans, path)
    result = load_tape(path)
    assert not result.errors
    assert [l.loan_id for l in result.loans] == [l.loan_id for l in synthetic_loans]
    for original, loaded in zip(synthetic_loans, result.loans):
        assert loaded.original_balance == pytest.approx(original.original_balance)
        assert loaded.current_noi == pytest.approx(original.current_noi, abs=0.01)
        assert loaded.note_rate == pytest.approx(original.note_rate, abs=1e-5)
