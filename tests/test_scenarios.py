import pytest

from refi_radar.models import PropertyType, Scenario
from refi_radar.scenarios import (
    PRESETS,
    UnknownScenarioError,
    apply_vacancy_shock,
    get_preset,
    stress_operations,
)


def test_vacancy_shock_holds_opex_fixed_creating_operating_leverage():
    # NOI 55 at 85% occupancy and 45% opex ratio => EGI 100, OpEx 45, potential gross 117.65.
    # At 70% occupancy EGI = 82.35, NOI = 37.35: a 15-point occupancy loss removes ~32% of NOI.
    result = apply_vacancy_shock(noi=55, occupancy=0.85, opex_ratio=0.45, occupancy_loss=0.15)
    assert result.occupancy == pytest.approx(0.70)
    assert result.noi == pytest.approx(100 / 0.85 * 0.70 - 45)
    assert result.noi / 55 - 1 == pytest.approx(-0.321, abs=1e-3)


def test_vacancy_shock_cannot_push_occupancy_negative():
    assert apply_vacancy_shock(noi=10, occupancy=0.10, opex_ratio=0.4, occupancy_loss=0.5).occupancy == 0


def test_office_shock_leaves_other_property_types_untouched():
    scenario = get_preset("office_vacancy_shock")
    for pt in PropertyType:
        ops = stress_operations(property_type=pt, noi=1_000_000, occupancy=0.9, opex_ratio=0.4, scenario=scenario)
        if pt is PropertyType.OFFICE:
            assert ops.noi < 1_000_000
        else:
            assert ops.noi == 1_000_000 and ops.occupancy == 0.9


def test_noi_haircut_applies_to_all_types_after_vacancy_shock():
    scenario = Scenario(name="t", description="", noi_haircut_pct=0.10, office_vacancy_shock_pp=0.15)
    office = stress_operations(
        property_type=PropertyType.OFFICE, noi=55, occupancy=0.85, opex_ratio=0.45, scenario=scenario
    )
    retail = stress_operations(
        property_type=PropertyType.RETAIL, noi=55, occupancy=0.85, opex_ratio=0.45, scenario=scenario
    )
    assert office.noi == pytest.approx((100 / 0.85 * 0.70 - 45) * 0.9)
    assert retail.noi == pytest.approx(49.5)


@pytest.mark.parametrize("loss", [0.0, 0.05, 0.10, 0.20, 0.30])
def test_more_vacancy_never_increases_noi(loss):
    worse = apply_vacancy_shock(noi=55, occupancy=0.85, opex_ratio=0.45, occupancy_loss=loss + 0.05)
    better = apply_vacancy_shock(noi=55, occupancy=0.85, opex_ratio=0.45, occupancy_loss=loss)
    assert worse.noi < better.noi


def test_presets_are_named_consistently_and_base_is_unstressed():
    assert all(name == s.name for name, s in PRESETS.items())
    base = get_preset("base")
    assert (base.rate_shock_bps, base.cap_rate_shock_bps, base.noi_haircut_pct, base.office_vacancy_shock_pp) == (
        0,
        0,
        0,
        0,
    )


def test_unknown_preset_lists_valid_options():
    with pytest.raises(UnknownScenarioError, match="base"):
        get_preset("apocalypse")
