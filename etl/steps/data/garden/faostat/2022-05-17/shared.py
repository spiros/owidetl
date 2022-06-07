"""Common processing of FAOSTAT datasets.

We have created a manual ranking of FAOSTAT flags. These flags are only used when there is ambiguity in the data,
namely, when there is more than one data value for a certain country-year-item-element-unit.
NOTES:
* We check that the definitions in our manual ranking agree with the ones provided by FAOSTAT.
* We do not include all flags: We include only the ones that solve an ambiguity in a particular case,
  and add more flags as we see need.
* We have found flags that appeared in a dataset, but were not included in the additional metadata
  (namely flag "R", found in qcl dataset, and "W" in rt dataset). These flags were added manually, using the definition
  in List / Flags in:
  https://www.fao.org/faostat/en/#definitions
* Other flags (namel "B", in rl dataset and "w" in rt dataset) were not found either in the additional metadata or in
  the website definitions. They have been assigned the description "Unknown flag".
* Unfortunately, flags do not remove all ambiguities: remaining duplicates are dropped without any meaningful criterion.

"""

import warnings
from copy import deepcopy
from pathlib import Path
from typing import List, cast, Any
from collections.abc import Callable

import numpy as np
import pandas as pd
from pandas.api.types import union_categoricals
from owid import catalog
from owid.datautils import geo

from etl.paths import DATA_DIR, STEP_DIR


NAMESPACE = Path(__file__).parent.parent.name
VERSION = Path(__file__).parent.name

# Maximum number of characters for item_code.
# FAOSTAT "item_code" is usually an integer number, however sometimes it has decimals and sometimes it contains letters.
# So we will convert it into a string of this number of characters (integers will be prepended with zeros).
N_CHARACTERS_ITEM_CODE = 8
# Maximum number of characters for element_code (integers will be prepended with zeros).
N_CHARACTERS_ELEMENT_CODE = 6
# Manual fixes to item codes to avoid ambiguities.
ITEM_AMENDMENTS = {
    "faostat_sdgb": [
        {
            "item_code": "AG_PRD_FIESMSN_",
            "fao_item": "2.1.2 Population in moderate or severe food insecurity (thousands of people) (female)",
            "new_item_code": "AG_PRD_FIESMSN_FEMALE",
            "new_fao_item": "2.1.2 Population in moderate or severe food insecurity (thousands of people) (female)",
        },
        {
            "item_code": "AG_PRD_FIESMSN_",
            "fao_item": "2.1.2 Population in moderate or severe food insecurity (thousands of people) (male)",
            "new_item_code": "AG_PRD_FIESMSN_MALE",
            "new_fao_item": "2.1.2 Population in moderate or severe food insecurity (thousands of people) (male)",
        },
    ],
    "faostat_fbsh": [
        # Mappings to harmonize item names of fbsh with those of fbs.
        {
            "item_code": "00002556",
            "fao_item": "Groundnuts (Shelled Eq)",
            "new_item_code": "00002552",
            "new_fao_item": "Groundnuts",
        },
        {
            "item_code": "00002805",
            "fao_item": "Rice (Milled Equivalent)",
            "new_item_code": "00002807",
            "new_fao_item": "Rice and products",
        }
    ],
}

# Regions to add to the data.
# TODO: Add region aggregates to relevant columns.
REGIONS_TO_ADD = [
    "North America",
    "South America",
    "Europe",
    "European Union (27)",
    "Africa",
    "Asia",
    "Oceania",
    "Low-income countries",
    "Upper-middle-income countries",
    "Lower-middle-income countries",
    "High-income countries",
]

# Rank flags by priority (where lowest index is highest priority).
# TODO: Discuss this flag ranking with others (they are quite arbitrary at the moment).
FLAGS_RANKING = (
    pd.DataFrame.from_records(
        columns=["flag", "description"],
        data=[
            (np.nan, "Official data"),
            ("F", "FAO estimate"),
            (
                "A",
                "Aggregate, may include official, semi-official, estimated or calculated data",
            ),
            ("Fc", "Calculated data"),
            (
                "I",
                "Country data reported by International Organizations where the country is a member (Semi-official) - WTO, EU, UNSD, etc.",
            ),
            (
                "W",
                "Data reported on country official publications or web sites (Official) or trade country files",
            ),
            ("Fm", "Manual Estimation"),
            ("Q", "Official data reported on FAO Questionnaires from countries"),
            ("*", "Unofficial figure"),
            ("Im", "FAO data based on imputation methodology"),
            ("M", "Data not available"),
            ("R", "Estimated data using trading partners database"),
            ("SD", "Statistical Discrepancy"),
            ("S", "Standardized data"),
            (
                "Qm",
                "Official data from questionnaires and/or national sources and/or COMTRADE (reporters)",
            ),
            ("Fk", "Calculated data on the basis of official figures"),
            ("Fb", "Data obtained as a balance"),
            ("E", "Expert sources from FAO (including other divisions)"),
            ("X", "International reliable sources"),
            ("Bk", "Break in series"),
            ("NV", "Data not available"),
            ("FC", "Calculated data"),
            (
                "Z",
                "When the Fertilizer Utilization Account (FUA) does not balance due to utilization from stockpiles, apparent consumption has been set to zero",
            ),
            ("P", "Provisional official data"),
            (
                "W",
                "Data reported on country official publications or web sites (Official) or trade country files",
            ),
            ("B", "Unknown flag"),
            ("w", "Unknown flag"),
            ("NR", "Not reported"),
            ("_P", "Provisional value"),
            ("_O", "Missing value"),
            ("_M", "Unknown flag"),
            ("_U", "Unknown flag"),
            ("_I", "Imputed value (CCSA definition)"),
            ("_V", "Unvalidated value"),
            ("_L", "Unknown flag"),
            ("_A", "Normal value"),
            ("_E", "Estimated value"),
            ("Cv", "Calculated through value"),
            # The definition of flag "_" exists, but it's empty.
            ("_", ""),
        ],
    )
    .reset_index()
    .rename(columns={"index": "ranking"})
)


def harmonize_items(df, dataset_short_name, item_col="item") -> pd.DataFrame:
    df = df.copy()
    # Note: Here list comprehension is faster than doing .astype(str).str.zfill(...).
    df["item_code"] = [str(item_code).zfill(N_CHARACTERS_ITEM_CODE) for item_code in df["item_code"]]
    df[item_col] = df[item_col].astype(str)

    # Fix those few cases where there is more than one item per item code within a given dataset.
    if dataset_short_name in ITEM_AMENDMENTS:
        for amendment in ITEM_AMENDMENTS[dataset_short_name]:
            df.loc[(df["item_code"] == amendment["item_code"]) &
                   (df[item_col] == amendment["fao_item"]), ("item_code", item_col)] = \
                (amendment["new_item_code"], amendment["new_fao_item"])

    # Convert both columns to category to reduce memory
    df = df.astype({
        'item_code': 'category',
        item_col: 'category'
    })

    return df


def harmonize_elements(df, element_col="element") -> pd.DataFrame:
    df = df.copy()
    df["element_code"] = [str(element_code).zfill(N_CHARACTERS_ELEMENT_CODE) for element_code in df["element_code"]]

    # Convert both columns to category to reduce memory
    df = df.astype({
        'element_code': 'category',
        element_col: 'category'
    })

    return df


def remove_rows_with_nan_value(
    data: pd.DataFrame, verbose: bool = False
) -> pd.DataFrame:
    """Remove rows for which column "value" is nan.

    Parameters
    ----------
    data : pd.DataFrame
        Data for current dataset.
    verbose : bool
        True to print information about the number and fraction of rows removed.

    Returns
    -------
    data : pd.DataFrame
        Data after removing nan values.

    """
    data = data.copy()
    # Number of rows with a nan in column "value".
    # We could also remove rows with any nan, however, before doing that, we would need to assign a value to nan flags.
    n_rows_with_nan_value = len(data[data["value"].isnull()])
    if n_rows_with_nan_value > 0:
        frac_nan_rows = n_rows_with_nan_value / len(data)
        if verbose:
            print(
                f"Removing {n_rows_with_nan_value} rows ({frac_nan_rows: .2%}) "
                f"with nan in column 'value'."
            )
        if frac_nan_rows > 0.15:
            warnings.warn(f"{frac_nan_rows: .0%} rows of nan values removed.")
        data = data.dropna(subset="value").reset_index(drop=True)

    return data


def remove_columns_with_only_nans(
    data: pd.DataFrame, verbose: bool = True
) -> pd.DataFrame:
    """Remove columns that only have nans.

    In principle, it should not be possible that columns have only nan values, but we use this function just in case.

    Parameters
    ----------
    data : pd.DataFrame
        Data for current dataset.
    verbose : bool
        True to print information about the removal of columns with nan values.

    Returns
    -------
    data : pd.DataFrame
        Data after removing columns of nans.

    """
    data = data.copy()
    # Remove columns that only have nans.
    columns_of_nans = data.columns[data.isnull().all(axis=0)]
    if len(columns_of_nans) > 0:
        if verbose:
            print(
                f"Removing {len(columns_of_nans)} columns ({len(columns_of_nans) / len(data.columns): .2%}) "
                f"that have only nans."
            )
        data = data.drop(columns=columns_of_nans)

    return data


def remove_duplicates(data: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """Remove rows with duplicated index (country, year, item, element, unit).

    First attempt to use flags to remove duplicates. If there are still duplicates, remove in whatever way possible.

    Parameters
    ----------
    data : pd.DataFrame
        Data for current dataset.
    verbose : bool
        True to print a summary of the removed duplicates.

    Returns
    -------
    data : pd.DataFrame
        Data (with a dummy numerical index) after removing duplicates.

    """
    data = data.copy()

    # Add flag ranking to dataset.
    data = pd.merge(
        data,
        FLAGS_RANKING[["flag", "ranking"]].rename(columns={"ranking": "flag_ranking"}),
        on="flag",
        how="left",
    ).astype({"flag": "category"})

    # Select columns that should be used as indexes.
    index_columns = [
        column
        for column in ["country", "year", "item", "element", "unit"]
        if column in data.columns
    ]

    # Number of ambiguous indices (those that have multiple data values).
    n_ambiguous_indices = len(data[data.duplicated(subset=index_columns, keep="first")])

    if n_ambiguous_indices > 0:
        # Number of ambiguous indices that cannot be solved using flags.
        n_ambiguous_indices_unsolvable = len(
            data[data.duplicated(subset=index_columns + ["flag_ranking"], keep="first")]
        )
        # Remove ambiguous indices (those that have multiple data values).
        # When possible, use flags to prioritise among duplicates.
        data = data.sort_values(index_columns + ["flag_ranking"]).drop_duplicates(
            subset=index_columns, keep="first"
        )
        frac_ambiguous = n_ambiguous_indices / len(data)
        frac_ambiguous_solved_by_flags = 1 - (
            n_ambiguous_indices_unsolvable / n_ambiguous_indices
        )
        if verbose:
            print(
                f"Removing {n_ambiguous_indices} ambiguous indices ({frac_ambiguous: .2%})."
            )
            print(
                f"{frac_ambiguous_solved_by_flags: .2%} of ambiguities were solved with flags."
            )

    data = data.drop(columns=["flag_ranking"])

    return data


def clean_year_column(year_column: pd.Series) -> pd.Series:
    """Clean year column.

    Year is given almost always as an integer value. But sometimes (e.g. in the faostat_fs dataset) it is a range of
    years (that differ by exactly 2 years, e.g. "2010-2012"). This function returns a series of integer years, which, in
    the cases where the original year was a range, corresponds to the mean of the range.

    Parameters
    ----------
    year_column : pd.Series
        Original column of year values (which may be integer, or ranges of values).

    Returns
    -------
    year_clean_series : pd.Series
        Clean column of years, as integer values.

    """
    year_clean = []
    for year in year_column:
        if "-" in str(year):
            year_range = year.split("-")
            year_min = int(year_range[0])
            year_max = int(year_range[1])
            assert year_max - year_min == 2
            year_clean.append(year_min + 1)
        else:
            year_clean.append(int(year))

    # Prepare series of integer year values.
    year_clean_series = pd.Series(year_clean)
    year_clean_series.name = "year"

    return year_clean_series


def add_custom_names_and_descriptions(data, items_metadata, elements_metadata):
    data = data.copy()

    error = f"There are missing item codes in metadata."
    assert set(data["item_code"]) <= set(items_metadata["item_code"]), error

    error = f"There are missing element codes in metadata."
    assert set(data["element_code"]) <= set(elements_metadata["element_code"]), error

    _expected_n_rows = len(data)
    data = pd.merge(data.rename(columns={"item": "fao_item"}),
                    items_metadata[['item_code', 'owid_item', 'owid_item_description']], on="item_code", how="left")
    assert len(data) == _expected_n_rows, f"Something went wrong when merging data with items metadata."

    data = pd.merge(data.rename(columns={"element": "fao_element", "unit": "fao_unit"}),
                    elements_metadata[['element_code', 'owid_element', 'owid_unit', 'owid_unit_factor',
                                       'owid_element_description', 'owid_unit_short_name']],
                    on=["element_code"], how="left")
    assert len(data) == _expected_n_rows, f"Something went wrong when merging data with elements metadata."

    # `category` type was lost during merge, convert it back
    data = data.astype({
        "element_code": "category",
        "item_code": "category",
    })

    # Remove "owid_" from column names.
    data = data.rename(columns={column: column.replace("owid_", "") for column in data.columns})

    return data


def clean_data(data: pd.DataFrame, items_metadata: pd.DataFrame, elements_metadata: pd.DataFrame,
               countries_file: Path) -> pd.DataFrame:
    """Process data (including harmonization of countries and regions) and prepare it for new garden dataset.

    Parameters
    ----------
    data : pd.DataFrame
        Unprocessed data for current dataset.
    countries_file : Path or str
        Path to mapping of country names.
    items_metadata : pd.DataFrame
        Items metadata (from the metadata dataset).
    elements_metadata : pd.DataFrame
        Elements metadata (from the metadata dataset).

    Returns
    -------
    data : pd.DataFrame
        Processed data, ready to be made into a table for a garden dataset.

    """
    data = data.copy()

    # Ensure column of values is numeric (transform any possible value like "<1" into a nan).
    data["value"] = pd.to_numeric(data["value"], errors="coerce")

    # Some datasets (at least faostat_fa) use "recipient_country" instead of "area". For consistency, change this.
    if "recipient_country" in data.columns:
        data = data.rename(
            columns={"recipient_country": "area", "recipient_country_code": "area_code"}
        )

    # Ensure year column is integer (sometimes it is given as a range of years, e.g. 2013-2015).
    data["year"] = clean_year_column(data["year"])

    # Remove rows with nan value.
    data = remove_rows_with_nan_value(data)

    # Use custom names for items, elements and units (and keep original names in "fao_*" columns).
    data = add_custom_names_and_descriptions(data, items_metadata, elements_metadata)

    # Harmonize country names.
    assert countries_file.is_file(), "countries file not found."
    data = geo.harmonize_countries(
        df=data,
        countries_file=str(countries_file),
        country_col="area",
        warn_on_unused_countries=False,
    ).rename(columns={"area": "country"}).astype({"country": "category"})
    # If countries are missing in countries file, execute etl.harmonize again and update countries file.

    # Sanity checks.

    # TODO: Properly deal with duplicates.
    print(f"WARNING: Temporarily removing areas with duplicates.")
    data = data[~data["country"].isin(["China", "Micronesia (country)"])].reset_index(drop=True)

    # TODO: Move this to remove_duplicates.
    n_countries_per_area_code = data.groupby("area_code")["country"].transform("nunique")
    ambiguous_area_codes = data[n_countries_per_area_code > 1][["area_code", "country"]].\
        drop_duplicates().set_index("area_code")["country"].to_dict()
    error = f"There cannot be multiple countries for the same area code. " \
            f"Redefine countries file for:\n{ambiguous_area_codes}."
    assert len(ambiguous_area_codes) == 0, error
    n_area_codes_per_country = data.groupby("country")["area_code"].transform("nunique")
    ambiguous_countries = data[n_area_codes_per_country > 1][["area_code", "country"]].\
        drop_duplicates().set_index("area_code")["country"].to_dict()
    error = f"There cannot be multiple area codes for the same countries. " \
            f"Redefine countries file for:\n{ambiguous_countries}."
    assert len(ambiguous_countries) == 0, error

    # TODO: Check for ambiguous indexes in the long table.
    # TODO: Check for ambiguous indexes in the wide table.

    # After harmonizing, there are some country-year with more than one item-element.
    # This happens for example because there is different data for "Micronesia" and "Micronesia (Federated States of)",
    # which are both mapped to the same country, "Micronesia (country)".
    # The same happens with "China", and "China, mainland".
    # TODO: Solve possible issue of duplicated regions in China
    # (https://github.com/owid/owid-issues/issues/130#issuecomment-1114859105).
    # In cases where a country-year has more than one item-element, try to remove duplicates by looking at the flags.
    # If flags do not remove the duplicates, raise an error.

    # Remove duplicated data points keeping the one with lowest ranking flag (i.e. highest priority).
    data = remove_duplicates(data)

    return data


def prepare_long_table(data: pd.DataFrame):
    # Set appropriate indexes.
    index_columns = ["area_code", "year", "item_code", "element_code"]
    if data.duplicated(subset=index_columns).any():
        warnings.warn("Index has duplicated keys.")
    data_long = data.set_index(index_columns, verify_integrity=True).sort_index()

    # Create new table with long data.
    data_table_long = catalog.Table(data_long).copy()

    return data_table_long


def concatenate(dfs: List[pd.DataFrame], **kwargs: Any) -> pd.DataFrame:
    """Concatenate while preserving categorical columns. Original [source code]
    (https://stackoverflow.com/a/57809778/1275818)."""
    # Iterate on categorical columns common to all dfs
    for col in set.intersection(
        *[
            set(df.select_dtypes(include='category').columns)
            for df in dfs
        ]
    ):
        # Generate the union category across dfs for this column
        uc = union_categoricals([df[col] for df in dfs])
        # Change to union category for all dataframes
        for df in dfs:
            df[col] = pd.Categorical(df[col].values, categories=uc.categories)
    return pd.concat(dfs, **kwargs)


def apply_on_categoricals(cat_series: List[pd.Series], func: Callable[..., str]) -> pd.Series:
    """Apply a function on a list of categorical series. This is much faster than converting
    them to strings first and then applying the function and it prevents memory explosion.

    It uses category codes instead of using values directly and it builds the output categorical
    mapping from codes to strings on the fly.

    Parameters
    ----------
    cat_series :
        List of categorical series.
    func :
        Function taking as many arguments as there are categorical series and returning str.

    Returns
    -------
    final_cat_series :
        Categorical series.

    """
    seen = {}
    codes = []
    categories = []
    for cat_codes in zip(*[s.cat.codes for s in cat_series]):
        if cat_codes not in seen:
            # add category
            cat_values = [s.cat.categories[code] for s, code in zip(cat_series, cat_codes)]
            categories.append(func(*cat_values))
            seen[cat_codes] = len(categories) - 1

        # use existing category
        codes.append(seen[cat_codes])

    final_cat_series = pd.Categorical.from_codes(codes, categories=categories)
    return cast(pd.Series, final_cat_series)


def prepare_wide_table(data: pd.DataFrame, dataset_title: str) -> catalog.Table:
    """Flatten a long table to obtain a wide table with ["country", "year"] as index.

    The input table will be pivoted to have [country, year] as index, and as many columns as combinations of
    item-element-unit entities.

    Parameters
    ----------
    data : pd.DataFrame
        Data for current domain.
    dataset_title : str
        Title for the dataset of current domain (only needed to include it in the name of the new variables).

    Returns
    -------
    wide_table : catalog.Table
        Data table with index [country, year].

    """
    data = data.copy()

    # Ensure "item" exists in data (there are some datasets where it may be missing).
    if "item" not in data.columns:
        data["item"] = ""

    # Construct a variable name that will not yield any possible duplicates.
    # This will be used as column names (which will then be formatted properly with underscores and lower case),
    # and also as the variable titles in grapher.
    # Also, for convenience, keep a similar structure as in the previous OWID dataset release.
    data["variable_name"] = apply_on_categoricals(
        [data.item, data.item_code, data.element, data.element_code, data.unit],
        lambda item, item_code, element, element_code, unit: f"{dataset_title} || {item} | {item_code} || {element} | {element_code} || {unit}"
    )

    # Construct a human-readable variable display name (which will be shown in grapher charts).
    data['variable_display_name'] = apply_on_categoricals([data.item, data.element, data.unit], lambda item, element, unit: f"{item} - {element} ({unit})")

    # Construct a human-readable variable description (for the variable metadata).
    data['variable_description'] = apply_on_categoricals([data.item_description, data.element_description], lambda item_desc, element_desc: f"{item_desc}\n{element_desc}".lstrip().rstrip())


    # Pivot over long dataframe to generate a wide dataframe with country-year as index, and as many columns as
    # unique elements in "variable_name" (which should be as many as combinations of item-elements).
    # Note: We include area_code in the index for completeness, but by construction country-year should not have
    # duplicates.
    # Note: `pivot` operation is usually faster on categorical columns
    data_pivot = data.pivot(index=["area_code", "country", "year"], columns=["variable_name"],
                            values=["value", "unit", "unit_short_name", "unit_factor", "variable_display_name",
                                    "variable_description"])

    # For convenience, create a dictionary for each zeroth-level multi-index.
    data_wide = {pivot_column: data_pivot[pivot_column] for pivot_column in data_pivot.columns.levels[0]}

    # Create a wide table with just the data values.
    wide_table = catalog.Table(data_wide["value"]).copy()

    # Add metadata to each new variable in the wide data table.
    for column in wide_table.columns:
        # Add variable name.
        wide_table[column].metadata.title = column

        # Add variable unit (long name).
        variable_unit = data_wide["unit"][column].dropna().unique()
        assert len(variable_unit) == 1
        wide_table[column].metadata.unit = variable_unit[0]

        # Add variable unit (short name).
        variable_unit_short_name = data_wide["unit_short_name"][column].dropna().unique()
        assert len(variable_unit_short_name) == 1
        wide_table[column].metadata.short_unit = variable_unit_short_name[0]

        # Add variable description.
        variable_description = data_wide["variable_description"][column].dropna().unique()
        assert len(variable_description) == 1
        wide_table[column].metadata.description = variable_description[0]

        # Add display parameters (for grapher).
        wide_table[column].metadata.display = {}
        # Display name.
        variable_display_name = data_wide["variable_display_name"][column].dropna().unique()
        assert len(variable_display_name) == 1
        wide_table[column].metadata.display["name"] = variable_display_name[0]
        # Unit conversion factor (if given).
        variable_unit_factor = data_wide["unit_factor"][column].dropna().unique()
        if len(variable_unit_factor) > 0:
            assert len(variable_unit_factor) == 1
            wide_table[column].metadata.display["conversionFactor"] = variable_unit_factor[0]

    # Sort columns and rows conveniently.
    wide_table = wide_table.reset_index().set_index(["country", "year"], verify_integrity=True)
    wide_table = wide_table[["area_code"] + sorted([column for column in wide_table.columns if column != "area_code"])]
    wide_table = wide_table.sort_index(level=["country", "year"]).sort_index()

    # Make all column names snake_case.
    # TODO: Add vertical bar to utils.underscore_table. When done, there will be no need to rename columns.
    wide_table = catalog.utils.underscore_table(
        wide_table.rename(columns={column: column.replace("|", "_") for column in wide_table.columns}))

    return wide_table


def run(dest_dir: str) -> None:
    ####################################################################################################################
    # Common definitions.
    ####################################################################################################################

    # Assume dest_dir is a path to the step that needs to be run, e.g. "faostat_qcl", and fetch namespace and dataset
    # short name from that path.
    dataset_short_name = Path(dest_dir).name
    # namespace = dataset_short_name.split("_")[0]
    # Path to latest dataset in meadow for current FAOSTAT domain.
    meadow_data_dir = sorted((DATA_DIR / "meadow" / NAMESPACE).glob(f"*/{dataset_short_name}"))[-1].parent /\
        dataset_short_name
    # Path to countries file.
    countries_file = STEP_DIR / "data" / "garden" / NAMESPACE / VERSION / f"{NAMESPACE}.countries.json"
    # Path to dataset of FAOSTAT metadata.
    garden_metadata_dir = DATA_DIR / "garden" / NAMESPACE / VERSION / f"{NAMESPACE}_metadata"

    ####################################################################################################################
    # Load data.
    ####################################################################################################################

    # Load meadow dataset and keep its metadata.
    dataset_meadow = catalog.Dataset(meadow_data_dir)
    # Load main table from dataset.
    data_table_meadow = dataset_meadow[dataset_short_name]
    data = pd.DataFrame(data_table_meadow).reset_index()

    # Load dataset of FAOSTAT metadata.
    metadata = catalog.Dataset(garden_metadata_dir)

    # Load and prepare dataset, items and element-units metadata.
    datasets_metadata = pd.DataFrame(metadata["datasets"]).reset_index()
    datasets_metadata = datasets_metadata[datasets_metadata["dataset"] == dataset_short_name].reset_index(drop=True)
    items_metadata = pd.DataFrame(metadata["items"]).reset_index()
    items_metadata = items_metadata[items_metadata["dataset"] == dataset_short_name].reset_index(drop=True)
    # TODO: Remove this line once items are stored with the right format.
    items_metadata["item_code"] = items_metadata["item_code"].astype(str).str.zfill(N_CHARACTERS_ITEM_CODE)
    elements_metadata = pd.DataFrame(metadata["elements"]).reset_index()
    elements_metadata = elements_metadata[elements_metadata["dataset"] == dataset_short_name].reset_index(drop=True)
    # TODO: Remove this line once elements are stored with the right format.
    elements_metadata["element_code"] = elements_metadata["element_code"].astype(str).str.zfill(
        N_CHARACTERS_ELEMENT_CODE)

    ####################################################################################################################
    # Process data.
    ####################################################################################################################

    # Harmonize items and elements, and clean data.
    data = harmonize_items(df=data, dataset_short_name=dataset_short_name)
    data = harmonize_elements(df=data)

    data = clean_data(data=data, items_metadata=items_metadata, elements_metadata=elements_metadata,
                      countries_file=countries_file)

    # TODO: Run more sanity checks (i.e. compare with previous version of the same domain).

    # Create a long table (with item code and element code as part of the index).
    data_table_long = prepare_long_table(data=data)

    # Create a wide table (with only country and year as index).
    data_table_wide = prepare_wide_table(data=data, dataset_title=datasets_metadata["owid_dataset_title"].item())

    ####################################################################################################################
    # Save outputs.
    ####################################################################################################################

    # Initialize new garden dataset.
    dataset_garden = catalog.Dataset.create_empty(dest_dir)
    # Prepare metadata for new garden dataset (starting with the metadata from the meadow version).
    dataset_garden_metadata = deepcopy(dataset_meadow.metadata)
    # TODO: Uncomment when datasets can have a version property:
    # dataset_garden_metadata.metadata.version = VERSION
    dataset_garden_metadata.description = datasets_metadata["owid_dataset_description"].item()
    dataset_garden_metadata.title = datasets_metadata["owid_dataset_title"].item()
    # Add metadata to dataset.
    dataset_garden.metadata = dataset_garden_metadata
    # Create new dataset in garden.
    dataset_garden.save()

    # Prepare metadata for new garden long table (starting with the metadata from the meadow version).
    data_table_long.metadata = deepcopy(data_table_meadow.metadata)
    data_table_long.metadata.title = dataset_garden_metadata.title
    data_table_long.metadata.description = dataset_garden_metadata.description
    data_table_long.metadata.primary_key = list(data_table_long.index.names)
    data_table_long.metadata.dataset = dataset_garden_metadata
    # Add long table to the dataset.
    dataset_garden.add(data_table_long)

    # Prepare metadata for new garden wide table (starting with the metadata from the long table).
    # Add wide table to the dataset.
    data_table_wide.metadata = deepcopy(data_table_long.metadata)

    data_table_wide.metadata.title += " - Flattened table indexed by country-year."
    data_table_wide.metadata.short_name += "_flat"
    data_table_wide.metadata.primary_key = list(data_table_wide.index.names)

    # Add wide table to the dataset.
    # TODO: Check why repack=True now fails.
    dataset_garden.add(data_table_wide, repack=False)
