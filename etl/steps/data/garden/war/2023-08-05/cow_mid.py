"""COW Militarised Inter-state Dispute dataset.


- This dataset only contains inter-state conflicts.

- We use the "fatality" level to differentiate different "types" of conflicts. The "fatality" level provides a range of fatalities (e.g. 1-25 deaths)

- Each entry in this dataset describes a conflict (its participants and period). Therefore we need to "explode" it to add observations
for each year of the conflict.

- Due to missing data in the number of deaths, we are not estimating this metric. Instead, we are using the "fatality" level to group by the different conflicts.

- We also do not report "number of new conflicts".

- The "number of ongoing conflicts" for a particular fatality can be understood as "the number of conflicts ongoing in a particular year that will have X fatalities
over their complete lifetime globally".
"""

from typing import cast

import numpy as np
import owid.catalog.processing as pr
import pandas as pd
from owid.catalog import Dataset, Table
from structlog import get_logger

from etl.helpers import PathFinder, create_dataset

# Get paths and naming conventions for current step.
paths = PathFinder(__file__)
# Log
log = get_logger()
# Mapping from fatality code to name
FATALITY_LEVEL_MAP = {
    0: "No deaths",
    1: "1-25 deaths",
    2: "26-100 deaths",
    3: "101-250 deaths",
    4: "251-500 deaths",
    5: "501-999 deaths",
    6: "> 999 deaths",
    -9: "Unknown",
}


def run(dest_dir: str) -> None:
    #
    # Load inputs.
    #
    # Load meadow dataset.
    ds_meadow = cast(Dataset, paths.load_dependency("cow_mid"))

    # Read table from meadow dataset.
    tb_a = ds_meadow["mida"].reset_index()
    tb_b = ds_meadow["midb"].reset_index()

    #
    # Process data.
    #
    log.info("war.cow_mid: read and process MIDA table")
    tb_a = process_mida_table(tb_a)

    log.info("war.cow_mid: read and process MIDB table")
    tb_b = process_midb_table(tb_b)

    log.info("war.cow_mid: combine tables")
    tb = combine_tables(tb_a, tb_b)

    # Estimate metrics
    log.info("war.cow_mid: estimate metrics")
    tb = estimate_metrics(tb)

    # Map fatality codes to names
    log.info("war.cow_mid: map fatality codes to names")
    tb["fatality"] = tb["fatality"].map(FATALITY_LEVEL_MAP)
    assert tb["fatality"].isna().sum() == 0, "Unmapped fatality codes!"

    # Add suffix with source name
    msk = tb["region"] != "World"
    tb.loc[msk, "region"] = tb.loc[msk, "region"] + " (COW)"

    log.info("war.cow_mid: replace NaNs with zeros where applicable")
    tb = replace_missing_data_with_zeros(tb)

    # Set index
    log.info("war.cow_mid: set index")
    tb = tb.set_index(["year", "region", "fatality"], verify_integrity=True)

    # Add short_name to table
    log.info("war.cow_mid: add shortname to table")
    tb = Table(tb, short_name=paths.short_name)

    #
    # Save outputs.
    #
    # Create a new garden dataset with the same metadata as the meadow dataset.
    ds_garden = create_dataset(dest_dir, tables=[tb], default_metadata=ds_meadow.metadata)

    # Save changes in the new garden dataset.
    ds_garden.save()


def process_mida_table(tb: Table) -> Table:
    """Process MIDA table.

    - Sanity checks
    - Keep relevant columns
    - Add observation per year
    """
    # Sanity checks
    assert tb["dispnum"].value_counts().max() == 1, "The same conflict (with same `dispnum`) appears multiple times"
    assert (tb["styear"] >= 0).all(), "NA values (or negative) found in `styear`"
    assert (tb["endyear"] >= 0).all(), "NA values (or negative) found in `endyear`"
    assert not set(tb["fatality"]) - set(FATALITY_LEVEL_MAP), "Unnexpected values for `fatality`!"

    # Keep relevant columns
    COLUMNS_RELEVANT = [
        "dispnum",
        "styear",
        "endyear",
        "fatality",
    ]
    tb = tb[COLUMNS_RELEVANT]

    # Add observation for each year
    tb = expand_observations(tb)

    # Drop columns
    tb = tb.drop(columns=["styear", "endyear"])

    return tb


def process_midb_table(tb: Table) -> Table:
    """Process MIDB table.

    - Sanity checks
    - Keep relevant columns
    - Add observation per year
    - Add regions
    """
    # Sanity checks
    assert (
        tb.groupby(["dispnum", "ccode", "styear", "endyear"]).size().max() == 1
    ), "Multiple entries for a conflict-country-start_year-end_year"
    assert tb["styear"].notna().all() and (tb["styear"] >= 0).all(), "NA values (or negative) found in `styear`"
    assert tb["endyear"].notna().all() and (tb["endyear"] >= 0).all(), "NA values (or negative) found in `endyear`"

    # Add regions
    tb = add_regions(tb)

    # Keep relevant columns
    COLUMNS_RELEVANT = [
        "dispnum",
        "styear",
        "endyear",
        "region",
    ]
    tb = tb[COLUMNS_RELEVANT]

    # Drop duplicates
    tb = tb.drop_duplicates()

    # Add observation for each year
    tb = expand_observations(tb)

    # Drop columns
    tb = tb.drop(columns=["styear", "endyear"])

    return tb


def combine_tables(tb_a: Table, tb_b: Table) -> Table:
    """Combine MIDA and MIDB processed tables.

    Basically, it adds region information (from MIDB) to MIDA.
    """
    # Merge
    tb = tb_a.merge(tb_b, on=["dispnum", "year"], how="left")

    # Fill NaNs
    ## Some disputes (identified by codes) have no region information in MIDB. We fill them manually.
    ## Sanity check (1)
    dispnum_nans_expected = {2044, 2328, 4005}  # dispute codes with no region information
    dispnum_nans_found = set(tb.loc[tb["region"].isna()])
    dispnum_nans_unexpected = dispnum_nans_found - dispnum_nans_expected
    assert dispnum_nans_unexpected, f"Unexpected dispnum with NaN regions: {dispnum_nans_unexpected}"
    ## Sanity check (2)
    assert (
        tb_b[tb_b["dispnum"].isin(dispnum_nans_expected)].groupby("dispnum")["region"].nunique().max() == 1
    ), f"More than one region for some dispnum in {dispnum_nans_expected}"
    ## Actually fill NaNs
    tb.loc[tb["dispnum"] == 2044, "region"] = "Americas"
    tb.loc[tb["dispnum"] == 2328, "region"] = "Europe"
    tb.loc[tb["dispnum"] == 4005, "region"] = "Asia"

    # Check there is no NaN!
    assert tb.notna().all().all(), "NaN in some field!"

    return tb


def add_regions(tb: Table) -> Table:
    """Assign region to each dispute-country pair.

    The region is assigned based on the country code (ccode) of the participant.
    """
    ## COW uses custom country codes, so we need the following custom mapping.
    tb.loc[(tb["ccode"] >= 1) & (tb["ccode"] <= 165), "region"] = "Americas"
    tb.loc[(tb["ccode"] >= 200) & (tb["ccode"] <= 395), "region"] = "Europe"
    tb.loc[(tb["ccode"] >= 400) & (tb["ccode"] <= 626), "region"] = "Africa"
    tb.loc[(tb["ccode"] >= 630) & (tb["ccode"] <= 698), "region"] = "Middle East"
    tb.loc[(tb["ccode"] >= 700) & (tb["ccode"] <= 990), "region"] = "Asia"

    # Sanity check: No missing regions
    assert tb["region"].notna().all(), f"Missing regions! {tb.loc[tb['region'].isna(), ['dispnum', 'ccode']]}"
    return tb


def expand_observations(tb: Table) -> Table:
    """Expand to have a row per (year, dispute).

    Example

        Input:

        | dispnum | year_start | year_end |
        |---------|------------|----------|
        | 1       | 1990       | 1993     |

        Output:

        |  year | warcode |
        |-------|---------|
        |  1990 |    1    |
        |  1991 |    1    |
        |  1992 |    1    |
        |  1993 |    1    |

    Parameters
    ----------
    tb : Table
        Original table, where each row is a dispute with its start and end year.

    Returns
    -------
    Table
        Here, each dispute has as many rows as years of activity. Its deaths have been uniformly distributed among the years of activity.
    """
    # Add missing years for each triplet ("warcode", "campcode", "ccode")
    YEAR_MIN = tb["styear"].min()
    YEAR_MAX = tb["endyear"].max()
    tb_all_years = pd.DataFrame(pd.RangeIndex(YEAR_MIN, YEAR_MAX + 1), columns=["year"])
    df = pd.DataFrame(tb)  # to prevent error "AttributeError: 'DataFrame' object has no attribute 'all_columns'"
    df = df.merge(tb_all_years, how="cross")  # type: ignore
    tb = Table(df, metadata=tb.metadata)
    # Filter only entries that actually existed
    tb = tb[(tb["year"] >= tb["styear"]) & (tb["year"] <= tb["endyear"])]

    return tb


def estimate_metrics(tb: Table) -> Table:
    """Remix table to have the desired metrics.

    These metrics are:
        - number_ongoing_disputes

    Parameters
    ----------
    tb : Table
        Table with a row per dispute and year of observation.

    Returns
    -------
    Table
        Table with a row per year, and the corresponding metrics of interest.
    """
    assert (
        tb.groupby(["dispnum"]).fatality.nunique().max() == 1
    ), "The same conflict appears with multiple fatality levels!"

    # Operations to apply
    ops = {"dispnum": "nunique"}

    # By regions
    tb_regions = tb.groupby(["year", "fatality", "region"], as_index=False).agg(ops)
    # World
    tb_world = tb.groupby(["year", "fatality"], as_index=False).agg(ops)
    tb_world["region"] = "World"

    # Combine
    tb = pr.concat([tb_regions, tb_world], ignore_index=False)

    # Field name
    tb = tb.rename(columns={"dispnum": "number_ongoing_disputes"})
    return tb


def replace_missing_data_with_zeros(tb: Table) -> Table:
    """Replace missing data with zeros.
    In some instances there is missing data. Instead, we'd like this to be zero-valued.
    """
    # Add missing (year, region, hostility_type) entries (filled with NaNs)
    years = np.arange(tb["year"].min(), tb["year"].max() + 1)
    regions = set(tb["region"])
    fatality_types = set(tb["fatality"])
    new_idx = pd.MultiIndex.from_product([years, regions, fatality_types], names=["year", "region", "fatality"])
    tb = tb.set_index(["year", "region", "fatality"], verify_integrity=True).reindex(new_idx).reset_index()

    # Change NaNs for 0 for specific rows
    ## For columns "number_ongoing_disputes", "number_new_disputes"
    columns = [
        "number_ongoing_disputes",
    ]
    tb.loc[:, columns] = tb.loc[:, columns].fillna(0)

    # Drop all-NaN rows
    tb = tb.dropna(subset=columns, how="all")
    return tb
