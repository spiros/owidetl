"""Load a garden dataset and create a grapher dataset."""

from etl.helpers import PathFinder, create_dataset

# Get paths and naming conventions for current step.
paths = PathFinder(__file__)


def run(dest_dir: str) -> None:
    #
    # Load inputs.
    #
    # Load garden dataset.
    ds_garden = paths.garden_dataset

    # Read table from garden dataset.
    tb = ds_garden["cigarette_sales"].reset_index()

    #
    # Process data.

    # include West Germany values for Germany 1945-1990
    tb = tb.replace("West Germany", "Germany")

    # Save outputs.
    #
    # Create a new grapher dataset with the same metadata as the garden dataset.
    tb = tb.format(["country", "year"])

    ds_grapher = create_dataset(
        dest_dir, tables=[tb], check_variables_metadata=True, default_metadata=ds_garden.metadata
    )

    # Save changes in the new grapher dataset.
    ds_grapher.save()
