import duckdb
import pandas as pd

from uk_address_matcher.analyse_results import (
    distinguishability_by_id,
    distinguishability_summary,
)
from uk_address_matcher.cleaning_pipelines import (
    clean_data_using_precomputed_rel_tok_freq,
)
from uk_address_matcher.splink_model_vs_canonical import (
    _performance_predict_against_canonical,
)

path = "/Users/robinlinacre/Documents/data_linking/address_matching_demos/address_table_example.ddb"
con = duckdb.connect(path)


p_ch = "./example_data/companies_house_addresess_postcode_overlap.parquet"
df_ch = con.read_parquet(p_ch).order("postcode").limit(100)

new_recs_clean = clean_data_using_precomputed_rel_tok_freq(df_ch, con=con)

# custom_in = pd.DataFrame([{"unique_id": "1", "source_dataset": "other", "address_concat": "102 Petty France", "postcode": "SW1H 9AJ"}])
# new_recs_clean = clean_data_using_precomputed_rel_tok_freq(pd.DataFrame(custom_in), con)


predictions = _performance_predict_against_canonical(
    df_addresses_to_match=new_recs_clean,
    con=con,
    match_weight_threshold=None,
    output_all_cols=True,
    include_full_postcode_block=False,
)

distinguishability_summary(
    df_predict=predictions, df_addresses_to_match=new_recs_clean, con=con
)

distinguishability_by_id(predictions, new_recs_clean, con)
