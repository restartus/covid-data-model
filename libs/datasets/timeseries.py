import warnings
import pathlib
from typing import List, Optional, Union, TextIO
import pandas as pd
import structlog
from covidactnow.datapublic import common_df
from covidactnow.datapublic.common_fields import COMMON_FIELDS_TIMESERIES_KEYS
from libs import us_state_abbrev
from libs.datasets import dataset_utils
from libs.datasets import dataset_base
from libs.datasets import custom_aggregations
from libs.datasets.common_fields import CommonIndexFields
from libs.datasets.common_fields import CommonFields
from libs.datasets.dataset_utils import AggregationLevel
from libs.qa.dataset_summary_gen import generate_field_summary


class DuplicateDataException(Exception):
    def __init__(self, message, duplicates):
        self.message = message
        self.duplicates = duplicates
        super().__init__()

    def __str__(self):
        return f"DuplicateDataException({self.message})"


class TimeseriesDataset(dataset_base.DatasetBase):
    """Represents timeseries dataset.

    To make a data source compatible with the timeseries, it must have the required
    fields in the Fields class below + metrics. The other fields are generated
    in the `from_source` method.
    """

    INDEX_FIELDS = [
        CommonIndexFields.DATE,
        CommonIndexFields.AGGREGATE_LEVEL,
        CommonIndexFields.COUNTRY,
        CommonIndexFields.STATE,
        CommonIndexFields.FIPS,
    ]

    COMMON_INDEX_FIELDS = COMMON_FIELDS_TIMESERIES_KEYS

    @property
    def all_fips(self):
        return self.data.reset_index().fips.unique()

    @property
    def states(self) -> List:
        return self.data[CommonFields.STATE].dropna().unique().tolist()

    @property
    def state_data(self) -> pd.DataFrame:
        return self.get_subset(AggregationLevel.STATE).data

    @property
    def county_data(self) -> pd.DataFrame:
        return self.get_subset(AggregationLevel.COUNTY).data

    def county_keys(self) -> List:
        """Returns a list of all (country, state, county) combinations."""
        # Check to make sure all values are county values
        warnings.warn(
            "Tell Tom you are using this, I'm going to delete it soon.",
            DeprecationWarning,
            stacklevel=2,
        )
        county_values = self.data[CommonFields.AGGREGATE_LEVEL] == AggregationLevel.COUNTY.value
        county_data = self.data[county_values]

        data = county_data.set_index(
            [CommonFields.COUNTRY, CommonFields.STATE, CommonFields.COUNTY, CommonFields.FIPS,]
        )
        values = set(data.index.to_list())
        return sorted(values)

    def latest_values(self, aggregation_level=None) -> pd.DataFrame:
        """Gets the most recent values.

        Args:
            aggregation_level: If specified, only gets latest values for that aggregation,
                otherwise returns values for entire aggretation.

        Return: DataFrame
        """
        if aggregation_level is None:
            county = self.latest_values(aggregation_level=AggregationLevel.COUNTY)
            state = self.latest_values(aggregation_level=AggregationLevel.STATE)
            return pd.concat([county, state])
        elif aggregation_level is AggregationLevel.COUNTY:
            group = [CommonFields.COUNTRY, CommonFields.STATE, CommonFields.FIPS]
        elif aggregation_level is AggregationLevel.STATE:
            group = [CommonFields.COUNTRY, CommonFields.STATE]
        else:
            assert aggregation_level is AggregationLevel.COUNTRY
            group = [CommonFields.COUNTRY]

        data = self.data[
            self.data[CommonFields.AGGREGATE_LEVEL] == aggregation_level.value
        ].reset_index()
        # If the groupby raises a ValueError check the dtype of date. If it was loaded
        # by read_csv did you set parse_dates=["date"]?
        return data.iloc[data.groupby(group).date.idxmax(), :]

    def get_date_columns(self) -> pd.DataFrame:
        ts_value_columns = (
            set(self.data.columns)
            - set(COMMON_FIELDS_TIMESERIES_KEYS)
            - set(dataset_utils.GEO_DATA_COLUMNS)
        )
        # Melt all the ts_value_columns into a single "value" column
        long = (
            self.data.loc[:, COMMON_FIELDS_TIMESERIES_KEYS + list(ts_value_columns)]
            .melt(id_vars=COMMON_FIELDS_TIMESERIES_KEYS, value_vars=ts_value_columns,)
            .dropna()
            .set_index([CommonFields.FIPS, "variable", CommonFields.DATE])
            .apply(pd.to_numeric)
        )
        # Unstack by DATE, creating a row for each timeseries and a column for each DATE.
        data_date_columns = long.unstack(CommonFields.DATE)
        # Drop any rows without a real value for any date.
        data_date_columns = data_date_columns.loc[
            data_date_columns.loc[:, "value"].notna().any(axis=1), :
        ]

        summary = data_date_columns.loc[:, "value"].apply(generate_field_summary, axis=1)

        geo_data_per_fips = dataset_utils.fips_index_geo_data(self.data)
        # Make a DataFrame with a row for each summary.index element
        assert summary.index.names == [CommonFields.FIPS, "variable"]
        geo_data = pd.merge(
            pd.DataFrame(data=[], index=summary.index),
            geo_data_per_fips,
            how="left",
            left_on=CommonFields.FIPS,  # FIPS is in the left MultiIndex
            right_index=True,
            suffixes=(False, False),
        )

        return pd.concat(
            {
                "geo_data": geo_data,
                "provenance": self.provenance,
                "summary": summary,
                "value": data_date_columns["value"],
            },
            axis=1,
        )

    def get_subset(
        self,
        aggregation_level=None,
        country=None,
        fips: Optional[str] = None,
        state: Optional[str] = None,
        states: Optional[List[str]] = None,
        on: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
    ) -> "TimeseriesDataset":
        """Fetch a new TimeseriesDataset with a subset of the data in `self`.

        Some parameters are only used in ipython notebooks."""
        row_binary_array = dataset_utils.make_binary_array(
            self.data,
            aggregation_level=aggregation_level,
            country=country,
            fips=fips,
            state=state,
            states=states,
            on=on,
            after=after,
            before=before,
        )
        return self.__class__(self.data.loc[row_binary_array, :])

    def get_records_for_fips(self, fips) -> List[dict]:
        """Get data for FIPS code.

        Args:
            fips: 2 digits for a state or 5 digits for a county

        Returns: List of dictionary records with NA values replaced to be None
        """
        return list(self.get_subset(fips=fips).yield_records())

    def get_data(
        self,
        aggregation_level=None,
        country=None,
        fips: Optional[str] = None,
        state: Optional[str] = None,
        states: Optional[List[str]] = None,
        on: Optional[str] = None,
        after: Optional[str] = None,
        before: Optional[str] = None,
        columns_slice: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        rows_binary_array = dataset_utils.make_binary_array(
            self.data,
            aggregation_level=aggregation_level,
            country=country,
            fips=fips,
            state=state,
            states=states,
            on=on,
            after=after,
            before=before,
        )
        if columns_slice is None:
            columns_slice = slice(None, None, None)
        return self.data.loc[rows_binary_array, columns_slice]

    @classmethod
    def from_source(
        cls, source: "DataSource", fill_missing_state: bool = True
    ) -> "TimeseriesDataset":
        """Loads data from a specific datasource.

        Args:
            source: DataSource to standardize for timeseries dataset
            fill_missing_state: If True, backfills missing state data by
                calculating county level aggregates.

        Returns: Timeseries object.
        """
        data = source.data
        # TODO(tom): Do this renaming upstream, when the source is loaded or when first copied from the third party.
        to_common_fields = {value: key for key, value in source.all_fields_map().items()}
        final_columns = to_common_fields.values()
        data = data.rename(columns=to_common_fields)[final_columns]
        group = [
            CommonFields.DATE,
            CommonFields.COUNTRY,
            CommonFields.AGGREGATE_LEVEL,
            CommonFields.STATE,
        ]
        data = custom_aggregations.update_with_combined_new_york_counties(
            data, group, are_boroughs_zero=source.HAS_AGGREGATED_NYC_BOROUGH
        )

        if fill_missing_state:
            state_groupby_fields = [
                CommonFields.DATE,
                CommonFields.COUNTRY,
                CommonFields.STATE,
            ]
            non_matching = dataset_utils.aggregate_and_get_nonmatching(
                data, state_groupby_fields, AggregationLevel.COUNTY, AggregationLevel.STATE,
            ).reset_index()
            data = pd.concat([data, non_matching])

        fips_data = dataset_utils.build_fips_data_frame()
        data = dataset_utils.add_county_using_fips(data, fips_data)
        is_state = data[CommonFields.AGGREGATE_LEVEL] == AggregationLevel.STATE.value
        state_fips = data.loc[is_state, CommonFields.STATE].map(us_state_abbrev.ABBREV_US_FIPS)
        data.loc[is_state, CommonFields.FIPS] = state_fips

        no_fips = data[CommonFields.FIPS].isnull()
        if no_fips.any():
            structlog.get_logger().warning(
                "Dropping rows without FIPS", source=str(source), rows=repr(data.loc[no_fips])
            )
            data = data.loc[~no_fips]

        dups = data.duplicated(COMMON_FIELDS_TIMESERIES_KEYS, keep=False)
        if dups.any():
            raise DuplicateDataException(f"Duplicates in {source}", data.loc[dups])

        # Choosing to sort by date
        data = data.sort_values(CommonFields.DATE)
        return cls(data)

    @classmethod
    def build_from_data_source(cls, source):
        """Build TimeseriesDataset from a data source."""
        if set(source.INDEX_FIELD_MAP.keys()) != set(cls.INDEX_FIELDS):
            raise ValueError("Index fields must match")

        return cls.from_source(source, fill_missing_state=source.FILL_MISSING_STATE_LEVEL_DATA)

    def to_latest_values_dataset(self):
        from libs.datasets.latest_values_dataset import LatestValuesDataset

        return LatestValuesDataset(self.latest_values())

    def summarize(self):
        dataset_utils.summarize(
            self.data,
            AggregationLevel.COUNTY,
            [CommonFields.DATE, CommonFields.COUNTRY, CommonFields.STATE, CommonFields.FIPS,],
        )

        dataset_utils.summarize(
            self.data,
            AggregationLevel.STATE,
            [CommonFields.DATE, CommonFields.COUNTRY, CommonFields.STATE],
        )

    @classmethod
    def load_csv(cls, path_or_buf: Union[pathlib.Path, TextIO]):
        df = common_df.read_csv(path_or_buf)
        # TODO: common_df.read_csv sets the index of the dataframe to be fips, date, however
        # most of the calling code expects fips and date to not be in an index.
        # In the future, it would be good to standardize around index fields.
        df = df.reset_index()
        return cls(df)

    def to_csv(self, path: pathlib.Path):
        """Persists timeseries to CSV.

        Args:
            path: Path to write to.
        """
        super().to_csv(path)
        self.get_date_columns().to_csv(
            str(path).replace(".csv", "-wide-dates.csv"),
            date_format="%Y-%m-%d",
            index=True,
            float_format="%.12g",
        )
