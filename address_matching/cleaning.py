import duckdb
from duckdb import DuckDBPyRelation

from .regexes import (
    construct_nested_call,
    move_flat_to_front,
    remove_apostrophes,
    remove_commas_periods,
    remove_multiple_spaces,
    remove_repeated_tokens,
    replace_fwd_slash_with_dash,
    standarise_num_dash_num,
    trim,
)


def upper_case_address_and_postcode(table_name: str) -> DuckDBPyRelation:
    sql = f"""
    select
        * exclude (address_concat, postcode),
        upper(address_concat) as address_concat,
        upper(postcode) as postcode
    from {table_name}
    """

    return duckdb.sql(sql)


def trim_whitespace_address_and_postcode(table_name: str) -> DuckDBPyRelation:
    sql = f"""
    select
        * exclude (address_concat, postcode),
        trim(address_concat) as address_concat,
        trim(postcode) as postcode
    from {table_name}
    """

    return duckdb.sql(sql)


def clean_address_string_first_pass(table_name: str) -> DuckDBPyRelation:
    fn_call = construct_nested_call(
        "address_concat",
        [
            remove_commas_periods,
            remove_apostrophes,
            remove_multiple_spaces,
            replace_fwd_slash_with_dash,
            standarise_num_dash_num,
            move_flat_to_front,
            remove_repeated_tokens,
            trim,
        ],
    )
    sql = f"""
    select
        * exclude (address_concat),
        {fn_call} as address_concat,

    from {table_name}
    """

    return duckdb.sql(sql)


def parse_out_numbers(table_name: str) -> DuckDBPyRelation:
    # Pattern to detect tokens with numbers in them inc e.g. 10A-20

    regex_pattern = r"\b(?:\d*[\w\-]*\d+[\w\-]*|\w(?:-\w)?)\b"
    sql = f"""
    SELECT
        * exclude (address_concat),
        regexp_replace(address_concat, '{regex_pattern}', '', 'g')
            AS address_without_numbers,
        regexp_extract_all(address_concat, '{regex_pattern}')
            AS numeric_tokens
        from {table_name}
        """
    return duckdb.sql(sql)


def clean_address_string_second_pass(table_name: str) -> DuckDBPyRelation:
    fn_call = construct_nested_call(
        "address_without_numbers",
        [remove_multiple_spaces, trim],
    )
    sql = f"""
    select
        * exclude (address_without_numbers),
        {fn_call} as address_without_numbers,

    from {table_name}
    """

    return duckdb.sql(sql)


def split_numeric_tokens_to_cols(table_name: str) -> DuckDBPyRelation:
    sql = f"""
    select
        * exclude (numeric_tokens),
        numeric_tokens[1] as numeric_token_1,
        numeric_tokens[2] as numeric_token_2,
        numeric_tokens[3] as numeric_token_3
    from {table_name}
    """

    return duckdb.sql(sql)


def tokenise_address_without_numbers(table_name: str) -> DuckDBPyRelation:
    sql = f"""
    select
        * exclude (address_without_numbers),
        regexp_split_to_array(trim(address_without_numbers), '\\s+')
            AS address_without_numbers_tokenised
    from {table_name}
    """

    return duckdb.sql(sql)


def add_term_frequencies_to_address_tokens(table_name: str) -> DuckDBPyRelation:
    # Compute relative term frequencies amongst the tokens
    sql = f"""
    WITH token_counts AS (
        SELECT
            token,
            count(*)  / sum(count(*)) OVER() as rel_freq
        FROM (
            SELECT
                unnest(address_without_numbers_tokenised) as token
            FROM {table_name}
        )
        GROUP BY token
    ),
    addresses_exploded AS (
        SELECT
            unique_id,
            unnest(address_without_numbers_tokenised) as token,
            row_number() OVER (PARTITION BY unique_id ORDER BY (SELECT NULL)) as token_order
        FROM {table_name}
    ),
    address_groups AS (
        SELECT addresses_exploded.*, token_counts.rel_freq
        FROM addresses_exploded
        LEFT JOIN token_counts
        ON addresses_exploded.token = token_counts.token

    ),
    token_freq_lookup AS (
        SELECT
            unique_id,
            list_zip(array_agg(token order by unique_id, token_order), array_agg(rel_freq order by unique_id, token_order)) as token_rel_freq_arr
        FROM address_groups
        GROUP BY unique_id


    )
    SELECT
        d.* EXCLUDE (address_without_numbers_tokenised),
        r.token_rel_freq_arr
    FROM
        {table_name} as d
    INNER JOIN token_freq_lookup as r
    ON d.unique_id = r.unique_id
    """

    return duckdb.sql(sql)


def first_unusual_token(table_name: str) -> DuckDBPyRelation:

    # Get first below freq
    first_token = "list_any_value(list_filter(token_rel_freq_arr, x -> x[2] < 0.01))"

    sql = f"""
    select
    {first_token} as first_unusual_token,
    *
    from {table_name}
    """
    return duckdb.sql(sql)


def use_first_unusual_token_if_no_numeric_token(table_name: str) -> DuckDBPyRelation:
    sql = f"""
    select
        * exclude (numeric_token_1, token_rel_freq_arr, first_unusual_token),
        case
            when numeric_token_1 is null then first_unusual_token[1]
            else numeric_token_1
            end as numeric_token_1,

    case
        when numeric_token_1 is null
        then list_filter(token_rel_freq_arr, x -> x[1] != first_unusual_token[1])
        else token_rel_freq_arr
    end
    as token_rel_freq_arr
    from {table_name}
    """

    return duckdb.sql(sql)


def final_column_order(table_name: str) -> DuckDBPyRelation:

    sql = f"""
    select
        unique_id,
        source_dataset,
        original_address_concat,
        numeric_token_1,
        numeric_token_2,
        numeric_token_3,
        list_transform(
            token_rel_freq_arr, x-> struct_pack(t:= x[1], v:= x[2])
        ) as token_rel_freq_arr,
        postcode
    from {table_name}
    """

    return duckdb.sql(sql)


def move_common_end_tokens_to_field(table_name: str) -> DuckDBPyRelation:
    # Want to put common tokens towards the end of the address
    # into their own field.  These tokens (e.g. SOMERSET or LONDON)
    # are often ommitted from so 'punishing' lack of agreement is probably
    # not necessary

    sql = """
    select array_agg(token) as end_tokens_to_remove
    from read_csv_auto("./common_tokens.csv")
    where token_count > 3000
    """
    common_end_tokens = duckdb.sql(sql)
    duckdb.register("common_end_tokens", common_end_tokens)

    penultimate_token_in_common_end_tokens = """
    list_contains(end_tokens_to_remove, address_without_numbers_tokenised[-2])
    """

    last_token_in_common_end_tokens = """
    list_contains(end_tokens_to_remove, address_without_numbers_tokenised[-1])
    """

    sql = f"""
    with joined as
    (
        select *
    from {table_name}
    cross join common_end_tokens
    )
    select
    * exclude (address_without_numbers_tokenised, end_tokens_to_remove),
    original_address_concat,
    case
    when
        ({penultimate_token_in_common_end_tokens} and {last_token_in_common_end_tokens})
        then address_without_numbers_tokenised[:-3]
    when {last_token_in_common_end_tokens}
        then address_without_numbers_tokenised[:-2]
    else address_without_numbers_tokenised
    end
    as address_without_numbers_tokenised,
    case
    when
        ({penultimate_token_in_common_end_tokens} and {last_token_in_common_end_tokens})
        then address_without_numbers_tokenised[-2:]
    when {last_token_in_common_end_tokens}
        then address_without_numbers_tokenised[-1:]
    else address_without_numbers_tokenised[0:0]
    end as common_end_tokens
    from joined
    """

    return duckdb.sql(sql)