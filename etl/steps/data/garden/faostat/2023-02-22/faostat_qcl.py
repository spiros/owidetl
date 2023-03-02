"""FAOSTAT garden step for faostat_qcl dataset."""

from pathlib import Path

import numpy as np
import pandas as pd
from owid import catalog
from owid.datautils import dataframes
from owid.datautils.io import load_json
from shared import (
    ADDED_TITLE_TO_WIDE_TABLE,
    CURRENT_DIR,
    FLAG_MULTIPLE_FLAGS,
    NAMESPACE,
    OUTLIERS_FILE_NAME,
    REGIONS_TO_ADD,
    add_per_capita_variables,
    add_regions,
    clean_data,
    harmonize_elements,
    harmonize_items,
    prepare_long_table,
    prepare_wide_table,
    remove_outliers,
)

from etl.helpers import PathFinder, create_dataset


def add_slaughtered_animals_to_meat_total(data: pd.DataFrame) -> pd.DataFrame:
    """Add number of slaughtered animals to meat total.

    There is no FAOSTAT data on slaughtered animals for total meat. We construct this data by aggregating that element
    for the items specified in items_to_aggregate (which corresponds to all meat items after removing redundancies).

    Parameters
    ----------
    data : pd.DataFrame
        Processed data where meat total does not have number of slaughtered animals.

    Returns
    -------
    combined_data : pd.DataFrame
        Data after adding the new variable.

    """
    # List of items to sum as part of "Meat, total" (avoiding double-counting items).
    items_to_aggregate = [
        "Meat, ass",
        "Meat, beef and buffalo",
        "Meat, camel",
        "Meat, horse",
        "Meat, lamb and mutton",
        "Meat, mule",
        "Meat, pig",
        "Meat, poultry",
        "Meat, rabbit",
        "Meat, sheep and goat",
    ]
    # OWID item name for total meat.
    total_meat_item = "Meat, total"
    # OWID element name, unit name, and unit short name for number of slaughtered animals.
    slaughtered_animals_element = "Producing or slaughtered animals"
    slaughtered_animals_unit = "animals"
    slaughtered_animals_unit_short_name = "animals"
    error = f"Some items required to get the aggregate '{total_meat_item}' are missing in data."
    assert set(items_to_aggregate) < set(data["item"]), error
    assert slaughtered_animals_element in data["element"].unique()
    assert slaughtered_animals_unit in data["unit"].unique()

    # For some reason, there are two element codes for the same element (they have different items assigned).
    error = "Element codes for 'Producing or slaughtered animals' may have changed."
    assert data[(data["element"] == slaughtered_animals_element) & ~(data["element_code"].str.contains("pc"))][
        "element_code"
    ].unique().tolist() == ["005320", "005321"], error

    # Similarly, there are two items for meat total.
    error = f"Item codes for '{total_meat_item}' may have changed."
    assert list(data[data["item"] == total_meat_item]["item_code"].unique()) == ["00001765"], error

    # We arbitrarily choose the first element code and the first item code.
    slaughtered_animals_element_code = "005320"
    total_meat_item_code = "00001765"

    # Check that, indeed, this variable is not given in the original data.
    assert data[
        (data["item"] == total_meat_item)
        & (data["element"] == slaughtered_animals_element)
        & (data["unit"] == slaughtered_animals_unit)
    ].empty

    # Select the subset of data to aggregate.
    data_to_aggregate = (
        data[
            (data["element"] == slaughtered_animals_element)
            & (data["unit"] == slaughtered_animals_unit)
            & (data["item"].isin(items_to_aggregate))
        ]
        .dropna(subset="value")
        .reset_index(drop=True)
    )

    # Create a dataframe with the total number of animals used for meat.
    animals = dataframes.groupby_agg(
        data_to_aggregate,
        groupby_columns=[
            "area_code",
            "fao_country",
            "fao_element",
            "country",
            "year",
            "population_with_data",
        ],
        aggregations={
            "value": "sum",
            "flag": lambda x: x if len(x) == 1 else FLAG_MULTIPLE_FLAGS,
        },
    ).reset_index()

    # Get element description for selected element code.
    _slaughtered_animals_element_description = data[data["element_code"] == slaughtered_animals_element_code][
        "element_description"
    ].unique()
    assert len(_slaughtered_animals_element_description) == 1
    slaughtered_animals_element_description = _slaughtered_animals_element_description[0]

    # Get item description for selected item code.
    _total_meat_item_description = data[data["item_code"] == total_meat_item_code]["item_description"].unique()
    assert len(_total_meat_item_description) == 1
    total_meat_item_description = _total_meat_item_description[0]

    # Get FAO item name for selected item code.
    _total_meat_fao_item = data[data["item_code"] == total_meat_item_code]["fao_item"].unique()
    assert len(_total_meat_fao_item) == 1
    total_meat_fao_item = _total_meat_fao_item[0]

    # Get FAO unit for selected item code.
    _total_meat_fao_unit = data[data["item_code"] == total_meat_item_code]["fao_unit_short_name"].unique()
    assert len(_total_meat_fao_unit) == 1
    total_meat_fao_unit = _total_meat_fao_unit[0]

    # Manually include the rest of columns.
    animals["element"] = slaughtered_animals_element
    animals["element_description"] = slaughtered_animals_element_description
    animals["unit"] = slaughtered_animals_unit
    animals["unit_short_name"] = slaughtered_animals_unit_short_name
    animals["element_code"] = slaughtered_animals_element_code
    animals["item_code"] = total_meat_item_code
    animals["item"] = total_meat_item
    animals["item_description"] = total_meat_item_description
    animals["fao_item"] = total_meat_fao_item
    animals["fao_unit_short_name"] = total_meat_fao_unit

    # Check that we are not missing any column.
    assert set(data.columns) == set(animals.columns)

    # Add animals data to the original dataframe.
    combined_data = (
        pd.concat([data, animals], ignore_index=True)
        .reset_index(drop=True)
        .astype(
            {
                "element_code": "category",
                "item_code": "category",
                "fao_item": "category",
                "fao_unit_short_name": "category",
                "flag": "category",
                "item": "category",
                "item_description": "category",
                "element": "category",
                "unit": "category",
                "element_description": "category",
                "unit_short_name": "category",
            }
        )
    )

    return combined_data


def add_yield_to_aggregate_regions(data: pd.DataFrame) -> pd.DataFrame:
    """Add yield (production / area harvested) to data for aggregate regions (i.e. continents and income groups).

    This data is not included in aggregate regions because it cannot be aggregated by simply summing the contribution of
    the individual countries. Instead, we need to aggregate production, then aggregate area harvested, and then divide
    one by the other.

    Note: Here, we divide production (the sum of the production from a list of countries in a region) by area (the sum
    of the area from a list of countries in a region) to obtain yield. But the list of countries that contributed to
    production may not be the same as the list of countries that contributed to area. We could impose that they must be
    the same, but this causes the resulting series to have gaps. Additionally, it seems that FAO also constructs yield
    in the same way. This was checked by comparing the resulting yield curves for 'Almonds' for all aggregate regions
    with their corresponding *(FAO) regions; they were identical.

    Parameters
    ----------
    data : pd.DataFrame
        Data that does not contain yield for aggregate regions.

    Returns
    -------
    combined_data : pd.DataFrame
        Data after adding yield.

    """
    # Element code of production, area harvested, and yield.
    production_element_code = "005510"
    area_element_code = "005312"
    yield_element_code = "005419"

    # Check that indeed regions do not contain any data for yield.
    assert data[(data["country"].isin(REGIONS_TO_ADD)) & (data["element_code"] == yield_element_code)].empty

    # Gather all fields that should stay the same.
    additional_fields = data[data["element_code"] == yield_element_code][
        [
            "element",
            "element_description",
            "fao_element",
            "fao_unit_short_name",
            "unit",
            "unit_short_name",
        ]
    ].drop_duplicates()
    assert len(additional_fields) == 1

    # Create a dataframe of production of regions.
    data_production = data[(data["country"].isin(REGIONS_TO_ADD)) & (data["element_code"] == production_element_code)]

    # Create a dataframe of area of regions.
    data_area = data[(data["country"].isin(REGIONS_TO_ADD)) & (data["element_code"] == area_element_code)]

    # Merge the two dataframes and create the new yield variable.
    merge_cols = [
        "area_code",
        "year",
        "item_code",
        "fao_country",
        "fao_item",
        "item",
        "item_description",
        "country",
    ]
    combined = pd.merge(
        data_production,
        data_area[merge_cols + ["flag", "value"]],
        on=merge_cols,
        how="inner",
        suffixes=("_production", "_area"),
    )

    combined["value"] = combined["value_production"] / combined["value_area"]

    # Replace infinities (caused by dividing by zero) by nan.
    combined["value"] = combined["value"].replace(np.inf, np.nan)

    # If both fields have the same flag, use that, otherwise use the flag of multiple flags.
    combined["flag"] = [
        flag_production if flag_production == flag_area else FLAG_MULTIPLE_FLAGS
        for flag_production, flag_area in zip(combined["flag_production"], combined["flag_area"])
    ]

    # Drop rows of nan and unnecessary columns.
    combined = combined.drop(columns=["flag_production", "flag_area", "value_production", "value_area"])
    combined = combined.dropna(subset="value").reset_index(drop=True)

    # Replace fields appropriately.
    combined["element_code"] = yield_element_code
    # Replace all other fields from the corresponding fields in yield (tonnes per hectare) variable.
    for field in additional_fields.columns:
        combined[field] = additional_fields[field].item()

    assert set(data.columns) == set(combined.columns)

    combined = combined

    combined_data = (
        pd.concat([data, combined], ignore_index=True)
        .reset_index(drop=True)
        .astype(
            {
                "element_code": "category",
                "fao_element": "category",
                "fao_unit_short_name": "category",
                "flag": "category",
                "element": "category",
                "unit": "category",
                "element_description": "category",
                "unit_short_name": "category",
            }
        )
    )

    return combined_data


def run(dest_dir: str) -> None:
    #
    # Load data.
    #
    # Fetch the dataset short name from dest_dir.
    dataset_short_name = Path(dest_dir).name

    # Define path to current step file.
    current_step_file = (CURRENT_DIR / dataset_short_name).with_suffix(".py")

    # Get paths and naming conventions for current data step.
    paths = PathFinder(current_step_file.as_posix())

    # Load latest meadow dataset and keep its metadata.
    ds_meadow: catalog.Dataset = paths.load_dependency(dataset_short_name)
    # Load main table from dataset.
    tb_meadow = ds_meadow[dataset_short_name]
    data = pd.DataFrame(tb_meadow).reset_index()

    # Load file of detected outliers.
    outliers = load_json(paths.directory / OUTLIERS_FILE_NAME)

    # Load dataset of FAOSTAT metadata.
    metadata: catalog.Dataset = paths.load_dependency(f"{NAMESPACE}_metadata")

    # Load dataset, items, element-units, and countries metadata.
    dataset_metadata = pd.DataFrame(metadata["datasets"]).loc[dataset_short_name].to_dict()
    items_metadata = pd.DataFrame(metadata["items"]).reset_index()
    items_metadata = items_metadata[items_metadata["dataset"] == dataset_short_name].reset_index(drop=True)
    elements_metadata = pd.DataFrame(metadata["elements"]).reset_index()
    elements_metadata = elements_metadata[elements_metadata["dataset"] == dataset_short_name].reset_index(drop=True)
    countries_metadata = pd.DataFrame(metadata["countries"]).reset_index()

    #
    # Process data.
    #
    # Harmonize items and elements, and clean data.
    data = harmonize_items(df=data, dataset_short_name=dataset_short_name)
    data = harmonize_elements(df=data)

    # Prepare data.
    data = clean_data(
        data=data,
        items_metadata=items_metadata,
        elements_metadata=elements_metadata,
        countries_metadata=countries_metadata,
    )

    # Include number of slaughtered animals in total meat (which is missing).
    data = add_slaughtered_animals_to_meat_total(data=data)

    # Add data for aggregate regions.
    data = add_regions(data=data, elements_metadata=elements_metadata)

    # Add per-capita variables.
    data = add_per_capita_variables(data=data, elements_metadata=elements_metadata)

    # Add yield (production per area) to aggregate regions.
    data = add_yield_to_aggregate_regions(data)

    # Remove outliers (this step needs to happen after creating regions and per capita variables).
    data = remove_outliers(data, outliers=outliers)

    # Create a long table (with item code and element code as part of the index).
    data_table_long = prepare_long_table(data=data)

    # Create a wide table (with only country and year as index).
    data_table_wide = prepare_wide_table(data=data)

    #
    # Save outputs.
    #
    # Update tables metadata.
    data_table_long.metadata.short_name = dataset_short_name
    data_table_long.metadata.title = dataset_metadata["owid_dataset_title"]
    data_table_wide.metadata.short_name = f"{dataset_short_name}_flat"
    data_table_wide.metadata.title = dataset_metadata["owid_dataset_title"] + ADDED_TITLE_TO_WIDE_TABLE
    # Initialise new garden dataset.
    ds_garden = create_dataset(
        dest_dir=dest_dir, tables=[data_table_long, data_table_wide], default_metadata=ds_meadow.metadata
    )
    # Update dataset metadata.
    ds_garden.metadata.description = dataset_metadata["owid_dataset_description"]
    ds_garden.metadata.title = dataset_metadata["owid_dataset_title"]
    # Create garden dataset.
    ds_garden.save()
