import importlib.resources as pkg_resources
import json
from typing import List

from duckdb import DuckDBPyConnection, DuckDBPyRelation
from splink.duckdb.linker import DuckDBLinker


def get_pretrained_linker(
    dfs: List[DuckDBPyRelation],
    *,
    con: DuckDBPyConnection,
    precomputed_numeric_tf_table: DuckDBPyRelation = None,
    additional_columns_to_retain: List[str] = None,
    salting_multiplier: int = None,
    include_full_postcode_block=False,
):
    # Load the settings file
    with pkg_resources.path(
        "uk_address_matcher.data", "splink_model.json"
    ) as settings_path:
        settings_as_dict = json.load(open(settings_path))

    if additional_columns_to_retain is not None:
        settings_as_dict["additional_columns_to_retain"] = additional_columns_to_retain

    new_rules = []
    for rule in settings_as_dict["blocking_rules_to_generate_predictions"]:
        if not include_full_postcode_block:
            if (
                isinstance(rule, dict)
                and rule["blocking_rule"] == "l.postcode = r.postcode"
            ):
                continue
        if isinstance(rule, str):
            new_rules.append(
                {"blocking_rule": rule, "salting_partitions": salting_multiplier}
            )
        if isinstance(rule, dict):
            new_rules.append(
                {
                    "blocking_rule": rule["blocking_rule"],
                    "salting_partitions": rule["salting_partitions"]
                    * salting_multiplier,
                }
            )
        settings_as_dict["blocking_rules_to_generate_predictions"] = new_rules

    dfs_pd = [d.df() for d in dfs]

    # Initialize the linker
    linker = DuckDBLinker(dfs_pd, settings_dict=settings_as_dict, connection=con)

    # Load the default term frequency table if none is provided
    if precomputed_numeric_tf_table is None:
        with pkg_resources.path(
            "uk_address_matcher.data", "numeric_token_frequencies.parquet"
        ) as default_tf_path:
            precomputed_numeric_tf_table = con.read_parquet(str(default_tf_path))

    if precomputed_numeric_tf_table is not None:
        for i in range(1, 4):
            df_sql = f"""
                select
                    numeric_token as numeric_token_{i},
                    tf_numeric_token as tf_numeric_token_{i}
                from precomputed_numeric_tf_table"""

            df = con.sql(df_sql).df()
            linker.register_term_frequency_lookup(
                df, f"numeric_token_{i}", overwrite=True
            )

    return linker
