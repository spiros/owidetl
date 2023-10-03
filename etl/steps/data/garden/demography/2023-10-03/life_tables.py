"""Load a meadow dataset and create a garden dataset.


Combines HMD and UN life tables.

Some notes:

    - Time coverage:
        - UN contains data on many more countries, but only since 1950.
        - HMD contains data on fewer countries, but since 1676!
        - We therefore use UN since 1950 for all countries, and HMD prior to that. We use the same source for all countries in each time period to ensure comparability across countries.
    - Age grous:
        - HMD contains single-age groups from 0 to 109 and 110+ (equivalent to >=110)
        - UN contains single-age groups from 0 to 99 and 100+ (equivalent to >=100)
"""

import owid.catalog.processing as pr
from owid.catalog import Table

from etl.helpers import PathFinder, create_dataset

# Get paths and naming conventions for current step.
paths = PathFinder(__file__)
# List of indicator columns
COLUMNS_INDICATORS = [
    "central_death_rate",
    "probability_of_death",
    "probability_of_survival",
    "number_survivors",
    "number_deaths",
    "number_person_years_lived",
    "survivorship_ratio",
    "number_person_years_remaining",
    "life_expectancy",
    "average_survival_length",
]
COLUMNS_INDEX = [
    "type",
    "location",
    "year",
    "sex",
    "age",
]


def run(dest_dir: str) -> None:
    #
    # Load inputs.
    #
    # Load meadow datasets.
    paths.log.info("load dataset, tables")
    ds_hmd = paths.load_dataset("hmd")
    ds_un = paths.load_dataset("un_wpp_lt")

    # Read table from meadow dataset.
    tb_hmd = ds_hmd["hmd"].reset_index()
    tb_un = ds_un["un_wpp_lt"].reset_index()

    #
    # Process data.
    #
    # Combine HMD + UN
    paths.log.info("concatenate tables")
    tb = combine_tables(tb_hmd, tb_un)

    # Set DTypes
    tb = tb.astype(
        {
            "age": str,
        }
    )

    # Set index
    tb = tb.set_index(COLUMNS_INDEX, verify_integrity=True)

    #
    # Save outputs.
    #
    # Create a new garden dataset with the same metadata as the meadow dataset.
    ds_garden = create_dataset(dest_dir, tables=[tb], check_variables_metadata=True)

    # Save changes in the new garden dataset.
    ds_garden.save()


def combine_tables(tb_hmd: Table, tb_un: Table) -> Table:
    """Combine HMD and UN life tables.

    - UN only provides period data.
    - We use UN data after 1950. Prior to that, we use HMD.
    - We considered using HMD over UN after 1950 if data was available for a given country for all years, ages and sexes.
        - However, this is only the case for very few countries: Australia, Germany, Hungary, Lithuania, Northern Ireland, Scotland, United Kingdom.
        - We decided against this to ensure comparability across countries (i.e. all countries use same source after 1950).
    """
    # HMD
    ## Get only format=1x1 in HMD, drop 'format' column
    tb_hmd = tb_hmd[tb_hmd["format"] == "1x1"].drop(columns=["format"])
    ## Ensure year is int
    tb_hmd["year"] = tb_hmd["year"].astype(str).astype("Int64")
    ## Sanity check years
    assert tb_hmd["year"].max() == 2022, "UN data should end in 2022"
    assert tb_hmd["year"].min() == 1676, "UN data should start in 1676"
    ## Keep only period data prior to 1950 (UN data starts in 1950)
    tb_hmd = tb_hmd[((tb_hmd["year"] < 1950) & (tb_hmd["type"] == "period")) | (tb_hmd["type"] == "cohort")]
    ## Column renames
    tb_hmd = tb_hmd.rename(
        columns={
            "country": "location",
        }
    )
    ## Filter relevant columns
    columns_indicators_hmd = [col for col in tb_hmd.columns if col in COLUMNS_INDICATORS]
    tb_hmd = tb_hmd[COLUMNS_INDEX + columns_indicators_hmd]

    # UN
    ## Set type='period' for UN data
    tb_un["type"] = "period"
    ## Sanity check years
    assert tb_un["year"].max() == 2021, "UN data should end in 2021"
    assert tb_un["year"].min() == 1950, "UN data should start in 1950"
    assert (tb_un["year"].drop_duplicates().diff().dropna() == 1).all(), "UN data should be yearly"
    ## Filter relevant columns
    tb_un = tb_un[COLUMNS_INDEX + COLUMNS_INDICATORS]

    # Combine tables
    tb = pr.concat([tb_hmd, tb_un], short_name=paths.short_name)

    # Remove all-NaN rows
    tb = tb.dropna(subset=COLUMNS_INDICATORS, how="all")

    return tb
