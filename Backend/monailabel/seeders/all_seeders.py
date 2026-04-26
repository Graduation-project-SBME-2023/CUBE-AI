from .model import seed_models

from .neuroblastoma_seeder import create_dummy_data, create_dummy_neuroblastoma_cases

def all_seeders():
    # create_dummy_data()
    # create_dummy_neuroblastoma_cases()
    seed_models()
