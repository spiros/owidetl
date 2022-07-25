"""Process the BP Statistical Review of World Energy 2022.

For the moment, this dataset is downloaded and processed by
https://github.com/owid/importers/tree/master/bp_statreview

However, in this additional step we add region aggregates following OWID definitions of regions.

"""

from copy import deepcopy
from typing import Dict, List, Optional, Union, cast

import numpy as np
import pandas as pd
from owid.datautils import geo

from owid import catalog
from shared import CURRENT_DIR, log

# Namespace and short name for output dataset.
NAMESPACE = "bp"
DATASET_SHORT_NAME = "bp_statistical_review"
# Path to metadata file for current dataset.
METADATA_FILE_PATH = CURRENT_DIR / "bp_statistical_review.meta.yml"
# Original BP's Statistical Review dataset name in the OWID catalog (without the institution and year).
BP_CATALOG_NAME = "statistical_review_of_world_energy"
BP_NAMESPACE_IN_CATALOG = "bp_statreview"
BP_VERSION = 2022
# Previous BP's Statistical Review dataset.
# It will be used to fill missing data in the new dataset.
BP_CATALOG_NAME_OLD = "statistical_review_of_world_energy"
BP_NAMESPACE_IN_CATALOG_OLD = "bp_statreview"
BP_VERSION_OLD = 2021

# Aggregate regions to add, following OWID definitions.
REGIONS_TO_ADD = {
    "North America": {
        "country_code": "OWID_NAM",
    },
    "South America": {
        "country_code": "OWID_SAM",
    },
    "Europe": {
        "country_code": "OWID_EUR",
    },
    # The EU27 is already included in the original BP data, with the same definition as OWID.
    # "European Union (27)": {
    #     "country_code": "OWID_EU27",
    # },
    "Africa": {
        "country_code": "OWID_AFR",
    },
    "Asia": {
        "country_code": "OWID_ASI",
    },
    "Oceania": {
        "country_code": "OWID_OCE",
    },
    "Low-income countries": {
        "country_code": "OWID_LIC",
    },
    "Upper-middle-income countries": {
        "country_code": "OWID_UMC",
    },
    "Lower-middle-income countries": {
        "country_code": "OWID_LMC",
    },
    "High-income countries": {
        "country_code": "OWID_HIC",
    },
}

# When creating region aggregates, decide how to distribute historical regions.
# The following decisions are based on the current location of the countries that succeeded the region, and their income
# group. Continent and income group assigned corresponds to the continent and income group of the majority of the
# population in the member countries.
HISTORIC_TO_CURRENT_REGION: Dict[str, Dict[str, Union[str, List[str]]]] = {
    "Netherlands Antilles": {
        "continent": "North America",
        "income_group": "High-income countries",
        "members": [
            # North America - High-income countries.
            "Aruba",
            "Curacao",
            "Sint Maarten (Dutch part)",
        ],
    },
    "USSR": {
        "continent": "Europe",
        "income_group": "Upper-middle-income countries",
        "members": [
            # Europe - High-income countries.
            "Lithuania",
            "Estonia",
            "Latvia",
            # Europe - Upper-middle-income countries.
            "Moldova",
            "Belarus",
            "Russia",
            # Europe - Lower-middle-income countries.
            "Ukraine",
            # Asia - Upper-middle-income countries.
            "Georgia",
            "Armenia",
            "Azerbaijan",
            "Turkmenistan",
            "Kazakhstan",
            # Asia - Lower-middle-income countries.
            "Kyrgyzstan",
            "Uzbekistan",
            "Tajikistan",
        ],
    },
}

# List of known overlaps between regions and member countries (or successor countries).
OVERLAPPING_DATA_TO_REMOVE_IN_AGGREGATES = [
    {
        "region": "USSR",
        "member": "Russia",
        "entity_to_make_nan": "region",
        "years": [1991, 1992, 1993, 1994, 1995, 1996],
        "variable": "Gas - Proved reserves",
    }
]

# True to ignore zeros when checking for overlaps between regions and member countries.
# This means that, if a region (e.g. USSR) and a member country or successor country (e.g. Russia) overlap, but in a
# variable that only has zeros, it will not be considered an overlap.
IGNORE_ZEROS_WHEN_CHECKING_FOR_OVERLAPPING_DATA = True

# We need to include the 'Other * (BP)' regions, otherwise continents have incomplete data.
# For example, when constructing the aggregate for Africa, we need to include 'Other Africa (BP)'.
# Otherwise we would be underestimating the region's total contribution.
ADDITIONAL_COUNTRIES_IN_REGIONS = {
    "Africa": [
        # Additional African regions in BP's data (e.g. 'Other Western Africa (BP)') seem to be included in
        # 'Other Africa (BP)', therefore we ignore them when creating aggregates.
        "Other Africa (BP)",
    ],
    "Asia": [
        # Adding 'Other Asia Pacific (BP)' may include areas of Oceania in Asia.
        # However, it seems that this region is usually significantly smaller than Asia.
        # So, we are possibly overestimating Asia, but not by a significant amount.
        "Other Asia Pacific (BP)",
        # Similarly, adding 'Other CIS (BP)' in Asia may include areas of Europe in Asia (e.g. Moldova).
        # However, since most countries in 'Other CIS (BP)' are Asian, adding it is more accurate than not adding it.
        "Other CIS (BP)",
        # Countries defined by BP in 'Middle East' are fully included in OWID's definition of Asia.
        "Other Middle East (BP)",
    ],
    "Europe": [
        "Other Europe (BP)",
    ],
    "North America": [
        "Other Caribbean (BP)",
        "Other North America (BP)",
    ],
    "South America": [
        "Other South America (BP)",
    ],
    # Given that 'Other Asia and Pacific (BP)' is often similar or even larger than Oceania, we avoid including it in
    # Oceania (and include it in Asia, see comment above).
    # This means that we may be underestimating Oceania by a significant amount, but BP does not provide unambiguous
    # data to avoid this.
    "Oceania": [],
}

# Variables that can be summed when constructing region aggregates.
# Biofuels in Africa have a non-zero total, while there is no contribution from African countries.
# This causes that our aggregate for 'Africa' would be zero, while the original 'Africa (BP)' is not.
# Also, biodiesels are only given for continents and a few countries.
# For this reason we avoid creating aggregates for biofuels and biodiesels.
AGGREGATES_BY_SUM = [
    "Carbon Dioxide Emissions",
    "Coal - Reserves - Anthracite and bituminous",
    "Coal - Reserves - Sub-bituminous and lignite",
    "Coal - Reserves - Total",
    "Coal Consumption - EJ",
    "Coal Consumption - TWh",
    "Coal Production - EJ",
    "Coal Production - TWh",
    "Coal Production - Tonnes",
    "Cobalt Production-Reserves",
    "Elec Gen from Coal",
    "Elec Gen from Gas",
    "Elec Gen from Oil",
    "Electricity Generation",
    "Gas - Proved reserves",
    "Gas Consumption - Bcf",
    "Gas Consumption - Bcm",
    "Gas Consumption - EJ",
    "Gas Consumption - TWh",
    "Gas Production - Bcf",
    "Gas Production - Bcm",
    "Gas Production - EJ",
    "Gas Production - TWh",
    "Geo Biomass Other - EJ",
    "Geo Biomass Other - TWh",
    "Graphite Production-Reserves",
    "Hydro Consumption - EJ",
    "Hydro Consumption - TWh",
    "Hydro Generation - TWh",
    "Lithium Production-Reserves",
    "Nuclear Consumption - EJ",
    "Nuclear Consumption - TWh",
    "Nuclear Generation - TWh",
    "Oil - Proved reserves",
    "Oil - Refinery throughput",
    "Oil - Refining capacity",
    "Oil Consumption - Barrels",
    "Oil Consumption - EJ",
    "Oil Consumption - TWh",
    "Oil Consumption - Tonnes",
    "Oil Production - Barrels",
    "Oil Production - Crude Conds",
    "Oil Production - NGLs",
    "Oil Production - TWh",
    "Oil Production - Tonnes",
    "Primary Energy Consumption - EJ",
    "Primary Energy Consumption - TWh",
    "Renewables Consumption - EJ",
    "Renewables Consumption - TWh",
    "Renewables Power - EJ",
    "Renewables power - TWh",
    "Solar Capacity",
    "Solar Consumption - EJ",
    "Solar Consumption - TWh",
    "Solar Generation - TWh",
    "Total Liquids - Consumption",
    "Wind Capacity",
    "Wind Consumption - EJ",
    "Wind Consumption - TWh",
    "Wind Generation - TWh",
    # 'Biofuels Consumption - Kboed - Total',
    # 'Biofuels Consumption - Kboed - Biodiesel',
    # 'Biofuels Consumption - PJ - Total',
    # 'Biofuels Consumption - PJ - Biodiesel',
    # 'Biofuels Consumption - TWh - Total',
    # 'Biofuels Consumption - TWh - Biodiesel',
    # 'Biofuels Consumption - TWh - Biodiesel (zero filled)',
    # 'Biofuels Consumption - TWh - Total (zero filled)',
    # 'Biofuels Production - Kboed - Total',
    # 'Biofuels Production - PJ - Total',
    # 'Biofuels Production - TWh - Total',
    # 'Biofuels Production - Kboed - Biodiesel',
    # 'Biofuels Production - PJ - Biodiesel',
    # 'Biofuels Production - TWh - Biodiesel',
    # 'Coal - Prices',
    # 'Coal Consumption - TWh (zero filled)',
    # 'Gas - Prices',
    # 'Gas Consumption - TWh (zero filled)',
    # 'Geo Biomass Other - TWh (zero filled)',
    # 'Hydro Consumption - TWh (zero filled)',
    # 'Nuclear Consumption - TWh (zero filled)',
    # 'Oil - Crude prices since 1861 (2021 $)',
    # 'Oil - Crude prices since 1861 (current $)',
    # 'Oil - Spot crude prices',
    # 'Oil Consumption - TWh (zero filled)',
    # 'Primary Energy - Cons capita',
    # 'Rare Earth Production-Reserves',
    # 'Solar Consumption - TWh (zero filled)',
    # 'Wind Consumption - TWh (zero filled)',
]


def load_income_groups() -> pd.DataFrame:
    """Load dataset of income groups and add historical regions to it.

    Returns
    -------
    income_groups : pd.DataFrame
        Income groups data.

    """
    income_groups = (
        catalog.find(
            table="wb_income_group",
            dataset="wb_income",
            namespace="wb",
            channels=["garden"],
        )
        .load()
        .reset_index()
    )
    # Add historical regions to income groups.
    for historic_region in HISTORIC_TO_CURRENT_REGION:
        historic_region_income_group = HISTORIC_TO_CURRENT_REGION[historic_region][
            "income_group"
        ]
        if historic_region not in income_groups["country"]:
            historic_region_df = pd.DataFrame(
                {
                    "country": [historic_region],
                    "income_group": [historic_region_income_group],
                }
            )
            income_groups = pd.concat(
                [income_groups, historic_region_df], ignore_index=True
            )

    return cast(pd.DataFrame, income_groups)


def detect_overlapping_data_for_regions_and_members(
    df: pd.DataFrame,
    index_columns: List[str],
    regions_and_members: Dict[str, Dict[str, Union[str, List[str]]]],
    known_overlaps: Optional[List[Dict[str, Union[str, List[int]]]]],
    ignore_zeros: bool = True,
) -> None:
    """Raise a warning if there is data for a particular region and for a country that is a member of that region.

    For example, if there is data for USSR and Russia on the same years, a warning will be raised.

    Parameters
    ----------
    df : pd.DataFrame
        Data.
    index_columns : list
        Names of columns that should be index of the data.
    regions_and_members : dict
        Regions and members (where each key corresponds to a region, and each region is a dictionary of various keys,
        one of which is 'members', which is a list of member countries).
    known_overlaps : list or None
        Instances of known overlaps in the data. If this function raises a warning, new instances should be added to the
        list.
    ignore_zeros : bool
        True to consider zeros in the data as missing values. Doing this, if a region has overlapping data with a member
        country, but one of their data points is zero, it will not be considered an overlap.

    """
    if known_overlaps is not None:
        df = df.copy()

        if ignore_zeros:
            # Replace zeros by nans, so that zeros are ignored when looking for overlapping data.
            overlapping_values_to_ignore = [0]
        else:
            overlapping_values_to_ignore = []

        regions = list(regions_and_members)
        for region in regions:
            # Create a dataframe with only data for the region, and remove columns that only have nans.
            # Optionally, replace zeros by nans, to also remove columns that only have zeros or nans.
            region_df = (
                df[df["country"] == region]
                .replace(overlapping_values_to_ignore, np.nan)
                .dropna(axis=1, how="all")
            )
            members = regions_and_members[region]["members"]
            for member in members:
                # Create a dataframe for this particular member country.
                member_df = (
                    df[df["country"] == member]
                    .replace(overlapping_values_to_ignore, np.nan)
                    .dropna(axis=1, how="all")
                )
                # Find common columns with (non-nan) data between region and member country.
                variables = [
                    column
                    for column in (set(region_df.columns) & set(member_df.columns))
                    if column not in index_columns
                ]
                for variable in variables:
                    # Concatenate region and member country's data for this variable.
                    combined = (
                        pd.concat(
                            [
                                region_df[["year", variable]],
                                member_df[["year", variable]],
                            ],
                            ignore_index=True,
                        )
                        .dropna()
                        .reset_index(drop=True)
                    )
                    # Find years where region and member country overlap.
                    overlapping = combined[combined.duplicated(subset="year")]
                    if not overlapping.empty:
                        overlapping_years = sorted(set(overlapping["year"]))
                        new_overlap = {
                            "region": region,
                            "member": member,
                            "years": overlapping_years,
                            "variable": variable,
                        }
                        # Check if the overlap found is already in the list of known overlaps.
                        # If this overlap is not known, raise a warning.
                        # Omit the field "entity_to_make_nan" when checking if this overlap is known.
                        _known_overlaps = [
                            {key for key in overlap if key != "entity_to_make_nan"}
                            for overlap in known_overlaps
                        ]
                        if new_overlap not in _known_overlaps:  # type: ignore
                            log.warning(
                                f"Data for '{region}' overlaps with '{member}' on '{variable}' "
                                f"and years: {overlapping_years}"
                            )


def remove_overlapping_data_for_regions_and_members(
    df: pd.DataFrame,
    known_overlaps: Optional[List[Dict[str, Union[str, List[int]]]]],
    country_col: str = "country",
    year_col: str = "year",
    ignore_zeros: bool = True,
) -> pd.DataFrame:
    """Check if list of known overlaps between region (e.g. a historical region like the USSR) and a member country (or
    a successor country, like Russia) do overlap, and remove them from the data.

    Parameters
    ----------
    df : pd.DataFrame
        Data.
    known_overlaps : list or None
        List of known overlaps between region and member country.
    country_col : str
        Name of country column.
    year_col : str
        Name of year column.
    ignore_zeros : bool
        True to ignore columns of zeros when checking if known overlaps are indeed overlaps.

    Returns
    -------
    df : pd.DataFrame
        Data after removing known overlapping rows between a region and a member country.

    """
    if known_overlaps is not None:
        df = df.copy()

        if ignore_zeros:
            overlapping_values_to_ignore = [0]
        else:
            overlapping_values_to_ignore = []

        for i, overlap in enumerate(known_overlaps):
            if set([overlap["region"], overlap["member"]]) <= set(df["country"]):
                # Check that the known overlap is indeed found in the data.
                duplicated_rows = (
                    df[(df[country_col].isin([overlap["region"], overlap["member"]]))][
                        [country_col, year_col, overlap["variable"]]
                    ]
                    .replace(overlapping_values_to_ignore, np.nan)
                    .dropna(subset=overlap["variable"])
                )
                duplicated_rows = duplicated_rows[
                    duplicated_rows.duplicated(subset="year", keep=False)
                ]
                overlapping_years = sorted(set(duplicated_rows["year"]))
                if overlapping_years != overlap["years"]:
                    log.warning(
                        f"Given overlap number {i} is not found in the data; redefine this list."
                    )
                # Make nan data points for either the region or the member (which is specified by "entity to make nan").
                indexes_to_make_nan = duplicated_rows[
                    duplicated_rows["country"] == overlap[overlap["entity_to_make_nan"]]  # type: ignore
                ].index.tolist()
                df.loc[indexes_to_make_nan, overlap["variable"]] = np.nan

    return df


def load_countries_in_regions() -> Dict[str, List[str]]:
    """Create a dictionary of regions (continents and income groups) and their member countries.

    Regions to include are defined above, in REGIONS_TO_ADD.
    Additional countries are added to regions following the definitions in ADDITIONAL_COUNTRIES_IN_REGIONS.

    Returns
    -------
    countries_in_regions : dict
        Dictionary of regions, where the value is a list of member countries in the region.

    """
    # Load income groups.
    income_groups = load_income_groups()

    countries_in_regions = {}
    for region in list(REGIONS_TO_ADD):
        # Add default OWID list of countries in region (which includes historical regions).
        countries_in_regions[region] = geo.list_countries_in_region(
            region=region, income_groups=income_groups
        )

    # Include additional countries in the region (if any given).
    for region in ADDITIONAL_COUNTRIES_IN_REGIONS:
        countries_in_regions[region] = (
            countries_in_regions[region] + ADDITIONAL_COUNTRIES_IN_REGIONS[region]
        )

    return countries_in_regions


def add_region_aggregates(
    data: pd.DataFrame,
    regions: List[str],
    index_columns: List[str],
    country_column: str = "country",
    year_column: str = "year",
    aggregates: Optional[Dict[str, str]] = None,
    known_overlaps: Optional[List[Dict[str, Union[str, List[int]]]]] = None,
    region_codes: Optional[List[str]] = None,
    country_code_column: str = "country_code",
) -> pd.DataFrame:
    """Add region aggregates for all regions (which may include continents and income groups).

    Parameters
    ----------
    data : pd.DataFrame
        Data.
    regions : list
        Regions to include.
    index_columns : list
        Name of index columns.
    country_column : str
        Name of country column.
    year_column : str
        Name of year column.
    aggregates : dict or None
        Dictionary of type of aggregation to use for each variable. If None, variables will be aggregated by summing.
    known_overlaps : list or None
        List of known overlaps between regions and their member countries.
    region_codes : list or None
        List of country codes for each new region. It must have the same number of elements, and in the same order, as
        the 'regions' argument.
    country_code_column : str
        Name of country codes column (only relevant of region_codes is not None).

    Returns
    -------
    data : pd.DataFrame
        Data after adding aggregate regions.

    """
    data = data.copy()

    if aggregates is None:
        # If aggregations are not specified, assume all variables are to be aggregated, by summing.
        aggregates = {
            column: "sum" for column in data.columns if column not in index_columns
        }
    # Get the list of regions to create, and their member countries.
    countries_in_regions = load_countries_in_regions()
    for region in regions:
        # List of countries in region.
        countries_in_region = countries_in_regions[region]
        # Select rows of data for member countries.
        data_region = data[data[country_column].isin(countries_in_region)]
        # Remove any known overlaps between regions (e.g. USSR, which is a historical region) in current region (e.g.
        # Europe) and their member countries (or successor countries, like Russia).
        # If any overlap in known_overlaps is not found, a warning will be raised.
        data_region = remove_overlapping_data_for_regions_and_members(
            df=data_region, known_overlaps=known_overlaps
        )

        # Check that there are no other overlaps in the data (after having removed the known ones).
        detect_overlapping_data_for_regions_and_members(
            df=data_region,
            regions_and_members=HISTORIC_TO_CURRENT_REGION,
            index_columns=index_columns,
            known_overlaps=known_overlaps,
        )

        # Add region aggregates.
        data_region = geo.add_region_aggregates(
            df=data_region,
            region=region,
            country_col=country_column,
            year_col=year_column,
            aggregations=aggregates,
            countries_in_region=countries_in_region,
            countries_that_must_have_data=[],
            frac_allowed_nans_per_year=None,
            num_allowed_nans_per_year=None,
        )
        data = pd.concat(
            [data, data_region[data_region[country_column] == region]],
            ignore_index=True,
        ).reset_index(drop=True)

    if region_codes is not None:
        # Add region codes to regions.
        if data[country_code_column].dtype == "category":
            data[country_code_column] = data[country_code_column].cat.add_categories(
                region_codes
            )
        for i, region in enumerate(regions):
            data.loc[
                data[country_column] == region, country_code_column
            ] = region_codes[i]

    return data


def prepare_output_table(df: pd.DataFrame, bp_table: catalog.Table) -> catalog.Table:
    """Create a table with the processed data, ready to be in a garden dataset and to be uploaded to grapher (although
    additional metadata may need to be added to the table).

    Parameters
    ----------
    df : pd.DataFrame
        Processed BP data.
    bp_table : catalog.Table
        Original table of BP statistical review data (used to transfer its metadata to the new table).

    Returns
    -------
    table : catalog.Table
        Table, ready to be added to a new garden dataset.

    """
    # Create new table.
    table = catalog.Table(df).copy()

    # Replace spurious inf values by nan.
    table = table.replace([np.inf, -np.inf], np.nan)

    # Sort conveniently and add an index.
    table = (
        table.sort_values(["country", "year"])
        .reset_index(drop=True)
        .set_index(["country", "year"], verify_integrity=True)
        .astype({"country_code": "category"})
    )

    # Convert column names to lower, snake case.
    table = catalog.utils.underscore_table(table)

    # Get the table metadata from the original table.
    table.metadata = deepcopy(bp_table.metadata)

    # Get the metadata of each variable from the original table.
    for column in table.drop(columns="country_code").columns:
        table[column].metadata = deepcopy(bp_table[column].metadata)

    return table


def fill_missing_values_with_previous_version(
    table: catalog.Table, table_old: catalog.Table
) -> catalog.Table:
    """Fill missing values in current data with values from the previous version of the dataset.

    Parameters
    ----------
    table : catalog.Table
        Processed data from current dataset.
    table_old : catalog.Table
        Processed data from previous dataset.

    Returns
    -------
    combined : catalog.Table
        Combined table, with data from the current data, but after filling missing values with data from the previous
        version of the dataset.

    """
    # For region aggregates, avoid filling nan with values from previous releases.
    # The reason is that aggregates each year may include data from different countries.
    # This is especially necessary in 2022 because regions had different definitions in 2021 (the ones by BP).
    # Remove region aggregates from the old table.
    table_old = (
        table_old.reset_index()
        .rename(columns={"entity_name": "country", "entity_code": "country_code"})
        .drop(columns=["entity_id"])
    )
    table_old = (
        table_old[~table_old["country"].isin(list(REGIONS_TO_ADD))]
        .reset_index(drop=True)
        .set_index(["country", "year"])
    )

    # Combine the current output table with the table from the previous version the dataset.
    combined = pd.merge(
        table,
        table_old.drop(columns="country_code"),
        left_index=True,
        right_index=True,
        how="left",
        suffixes=("", "_old"),
    )

    # List the common columns that can be filled with values from the previous version.
    columns = [column for column in combined.columns if column.endswith("_old")]

    # Fill missing values in the current table with values from the old table.
    for column_old in columns:
        column = column_old.replace("_old", "")
        combined[column] = combined[column].fillna(combined[column_old])
    # Remove columns from the old table.
    combined = combined.drop(columns=columns)

    # Transfer metadata from the table of the current dataset into the combined table.
    combined.metadata = deepcopy(table.metadata)
    # When that is not possible (for columns that were only in the old but not in the new table),
    # get the metadata from the old table.

    for column in combined.columns:
        try:
            combined[column].metadata = deepcopy(table[column].metadata)
        except KeyError:
            combined[column].metadata = deepcopy(table_old[column].metadata)

    # Sanity checks.
    assert len(combined) == len(table)
    assert set(table.columns) <= set(combined.columns)

    return combined


def amend_zero_filled_variables_for_region_aggregates(df: pd.DataFrame) -> pd.DataFrame:
    """Fill the "* (zero filled)" variables (which were ignored when creating aggregates) with the new aggregate data,
    and fill any possible nan with zeros.

    Parameters
    ----------
    df : pd.DataFrame
        Data after having created region aggregates (which ignore '* (zero filled)' variables).

    Returns
    -------
    df : pd.DataFrame
        Data after amending zero filled variables for region aggregates.

    """
    df = df.copy()

    zero_filled_variables = [
        column for column in df.columns if "(zero filled)" in column
    ]
    original_variables = [
        column.replace(" (zero filled)", "")
        for column in df.columns
        if "(zero filled)" in column
    ]
    select_regions = df["country"].isin(REGIONS_TO_ADD)
    df.loc[select_regions, zero_filled_variables] = (
        df[select_regions][original_variables].fillna(0).values
    )

    return df


def run(dest_dir: str) -> None:
    #
    # Load data.
    #
    # Load table from latest BP dataset.
    bp_table = catalog.find_one(
        BP_CATALOG_NAME,
        channels=["backport"],
        namespace=f"{BP_NAMESPACE_IN_CATALOG}@{BP_VERSION}",
    )

    # Load previous version of the BP energy mix dataset, that will be used at the end to fill missing values in the
    # current dataset.
    bp_table_old = catalog.find_one(
        BP_CATALOG_NAME_OLD,
        channels=["backport"],
        namespace=f"{BP_NAMESPACE_IN_CATALOG_OLD}@{BP_VERSION_OLD}",
    )

    #
    # Process data.
    #
    # Extract dataframe of BP data from table.
    bp_data = (
        pd.DataFrame(bp_table)
        .reset_index()
        .rename(
            columns={
                column: bp_table[column].metadata.title for column in bp_table.columns
            }
        )
        .rename(columns={"entity_name": "country", "entity_code": "country_code"})
        .drop(columns="entity_id")
    )

    # Add region aggregates.
    df = add_region_aggregates(
        data=bp_data,
        regions=list(REGIONS_TO_ADD),
        index_columns=["country", "year", "country_code"],
        country_column="country",
        year_column="year",
        aggregates={column: "sum" for column in AGGREGATES_BY_SUM},
        known_overlaps=OVERLAPPING_DATA_TO_REMOVE_IN_AGGREGATES,  # type: ignore
        region_codes=[
            REGIONS_TO_ADD[region]["country_code"] for region in REGIONS_TO_ADD
        ],
    )

    # Fill nans with zeros for "* (zero filled)" variables for region aggregates (which were ignored).
    df = amend_zero_filled_variables_for_region_aggregates(df)

    # Prepare output data in a convenient way.
    table = prepare_output_table(df, bp_table)

    # Fill missing values in current table with values from the previous dataset, when possible.
    combined = fill_missing_values_with_previous_version(
        table=table, table_old=bp_table_old
    )

    #
    # Save outputs.
    #
    # Initialize new garden dataset.
    dataset = catalog.Dataset.create_empty(dest_dir)
    # Add metadata to dataset.
    dataset.metadata.update_from_yaml(METADATA_FILE_PATH)
    # Create new dataset in garden.
    dataset.save()

    # Add table to the dataset.
    combined.metadata.title = dataset.metadata.title
    combined.metadata.description = dataset.metadata.description
    combined.metadata.dataset = dataset.metadata
    combined.metadata.short_name = dataset.metadata.short_name
    combined.metadata.primary_key = list(combined.index.names)
    dataset.add(combined, repack=True)
