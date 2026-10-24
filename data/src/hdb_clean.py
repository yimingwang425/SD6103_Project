import os
import pandas as pd
import numpy as np
from sqlalchemy import create_engine, text

file_path = '../raw_data/hdb.csv'

def load_hdb_data(file_path):
    hdb_df = pd.read_csv(file_path)
    return hdb_df

def build_town():
    town_df = (
        load_hdb_data(file_path)
        .loc[:,["PLN_AREA_N"]]
        .drop_duplicates()
        .dropna()
        .reset_index(drop=True)
    )
    town_df = town_df.rename(columns={"PLN_AREA_N": "town_name"})
    return town_df

def load_town(town_df,engine):
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE TABLE town RESTART IDENTITY"))
        town_df.to_sql("town", conn, if_exists="append", index=False)