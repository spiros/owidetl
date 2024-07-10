"""Load a meadow dataset and create a garden dataset."""

from etl.helpers import PathFinder, create_dataset

# Get paths and naming conventions for current step.
paths = PathFinder(__file__)


def run(dest_dir: str) -> None:
    #
    # Load inputs.
    #
    # Retrieve snapshot.
    snap = paths.load_snapshot("ai_phds.csv")

    # Load data from snapshot.
    tb = snap.read()
    #
    # Process data.
    #
    tb["value"] = tb["value"].str.replace("%", "")
    tb = tb.pivot(index=["Year"], columns="indicator", values="value").reset_index()
    tb["country"] = "United States and Canada"
    tb = tb.format(["country", "year"])
    #
    # Save outputs.
    #
    # Create a new garden dataset with the same metadata as the meadow dataset.
    ds_garden = create_dataset(dest_dir, tables=[tb], check_variables_metadata=True, default_metadata=snap.metadata)

    # Save changes in the new garden dataset.
    ds_garden.save()
