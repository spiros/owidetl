"""Script to create a snapshot of dataset 'Global hen inventory'.

The data is manually downloaded from the first Tableau dashboard shown in:
https://welfarefootprint.org/research-projects/laying-hens/

To download the data:
* Go to: https://public.tableau.com/views/GlobalHenInventory-Reduced/Dashboard1
* Click on the download button on the bottom-right corner of the dashboard.
* Select "Data" in the pop-up message, which will open another window showing a data table.
* In that new window, there is a button "Show fields" on the upper-right corner of the window. Select all fields.
* Next to that button, click on "Download", which will download a csv file.

Then execute this script with the argument --path-to-file followed by the path to the downloaded file.

"""

from pathlib import Path

import click

from etl.snapshot import Snapshot

# Version for current snapshot dataset.
SNAPSHOT_VERSION = Path(__file__).parent.name


@click.command()
@click.option(
    "--upload/--skip-upload",
    default=True,
    type=bool,
    help="Upload dataset to Snapshot",
)
@click.option("--path-to-file", prompt=True, type=str, help="Path to local data file.")
def main(path_to_file: str, upload: bool) -> None:
    # Create a new snapshot.
    snap = Snapshot(f"animal_welfare/{SNAPSHOT_VERSION}/global_hen_inventory.csv")

    # Ensure destination folder exists.
    snap.path.parent.mkdir(exist_ok=True, parents=True)

    # Copy local data file to snapshots data folder.
    snap.path.write_bytes(Path(path_to_file).read_bytes())

    # Add file to DVC and upload to S3.
    snap.dvc_add(upload=upload)


if __name__ == "__main__":
    main()
