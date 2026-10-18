"""Run after installing the package; all example values are invented."""

from pathlib import Path

from eighteenbirdies import EighteenBirdiesClient


def main():
    sample = Path(__file__).with_name("synthetic-export.json")
    client = EighteenBirdiesClient.from_export(sample)
    print(client.summary())
    print(client.coverage())


if __name__ == "__main__":
    main()
