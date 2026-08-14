"""Run all idempotent Application API seeders."""

from application_api.seeders.all_seeders import all_seeders


if __name__ == "__main__":
    all_seeders()
