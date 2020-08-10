import pandas as pd

from covidactnow.datapublic.common_fields import CommonFields
from libs.datasets import combined_datasets
from libs.datasets.timeseries import TimeseriesDataset


def calculate_top_level_metrics_for_fips(fips: str):
    timeseries = combined_datasets.load_us_timeseries_dataset()
    latest = combined_datasets.load_us_latest_dataset()

    fips_timeseries = timeseries.get_subset(fips=fips)
    fips_record = latest.get_record_for_fips(fips)

    # not sure of return type for now, could be a dictionary, or maybe it would be more effective
    # as a pandas dataframe with a column for each metric.
    return calculate_top_level_metrics_for_timeseries(fips_timeseries, fips_record)


def calculate_top_level_metrics_for_timeseries(timeseries: TimeseriesDataset, latest: dict):
    # Making sure that the timeseries object passed in is only for one fips.
    assert len(timeseries.all_fips) == 1
    population = latest[CommonFields.POPULATION]
    cases = timeseries.data[CommonFields.CASES]
    neg_tests = timeseries.data[CommonFields.NEGATIVE_TESTS]

    case_density = calculate_case_density(cases=cases, population=population)
    test_positivity = calculate_test_positivity(pos_cases=cases, neg_tests=neg_tests)

    return {"case_density": case_density, "test_positivity": test_positivity}


def calculate_case_density(
    cases: pd.Series, population: int, smooth: int = 7, normalize_by: int = 100000
) -> pd.Series:
    """
    Calculates normalized cases density.

    Args:
        cases: Number of cases in a given fips.
        population: Population for a given fips.
        normalized_by: Normalize data by a constant.

    Returns:
        Population cases density.
    """
    smoothed = cases.rolling(smooth).mean()
    return smoothed / (population / normalize_by)


def calculate_test_positivity(
    pos_cases: pd.Series, neg_tests: pd.Series, smooth: int = 7, lag_lookback: int = 7
) -> pd.Series:
    """
    Calculates positive test rate.

    Args:
        pos_cases: Number of positive cases.
        neg_tests: Number of negative cases.

    Returns:
        Positive test rate.
    """
    pos_smoothed = pos_cases.rolling(smooth).mean()
    neg_smoothed = neg_tests.rolling(smooth).mean()

    last_n_pos = pos_smoothed[-lag_lookback:]
    last_n_neg = neg_smoothed[-lag_lookback:]
    # TODO: Porting from: https://github.com/covid-projections/covid-projections/blob/master/src/common/models/Projection.ts#L521.
    # Do we still want to return no data if there appears to be positive case data but lagging data for negative cases?
    if any(last_n_pos) and last_n_neg.isna().all():
        return pd.Series([], dtype="float64")
    return pos_smoothed / (neg_smoothed + pos_smoothed)


# Example of running calculation for all counties in a state, using the latest dataset
# to get all fips codes for that state
def calculate_metrics_for_counties_in_state(state: str):
    latest = combined_datasets.load_us_latest_dataset()
    state_latest_values = latest.county.get_subset(state=state)
    for fips in state_latest_values.all_fips:
        yield calculate_top_level_metrics_for_fips(fips)
