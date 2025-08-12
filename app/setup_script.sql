-- This is the setup script that runs while installing a Snowflake Native App in a consumer account.
-- For more information on how to create setup file, visit https://docs.snowflake.com/en/developer-guide/native-apps/creating-setup-script

-- A general guideline to building this script looks like:
-- 1. Create application roles
CREATE APPLICATION ROLE IF NOT EXISTS app_public;

-- 2. Create a versioned schema to hold those UDFs/Stored Procedures
CREATE OR ALTER VERSIONED SCHEMA core;
GRANT USAGE ON SCHEMA core TO APPLICATION ROLE app_public;

-- 3. Create UDFs and Stored Procedures using the python code you wrote in src/module-add, as shown below.

CREATE OR REPLACE PROCEDURE core.table_chunker()
RETURNS STRING
LANGUAGE SQL
AS
$$
BEGIN
    EXECUTE IMMEDIATE '
        CREATE OR REPLACE TABLE movies.data.movies_metadata_chunked AS
        SELECT
            m.*,
            COALESCE(m.title, '''') || ''\n'' ||
            COALESCE(m.budget, '''') || ''\n'' ||
            COALESCE(m.popularity, '''') || ''\n'' ||
            COALESCE(m.runtime, '''') || ''\n'' ||
            COALESCE(m.release_date, '''') || ''\n'' ||
            f.value AS chunk
        FROM movies.data.movies_raw m,
        LATERAL FLATTEN(
            INPUT => snowflake.cortex.split_text_recursive_character(
                COALESCE(m.overview, ''''),
                CAST(NULL AS STRING),
                2000,
                300
            )
        ) f
    ';

    GRANT SELECT ON TABLE movies.data.movies_metadata_chunked TO APPLICATION ROLE app_public;

    RETURN 'Table Chunked!';
END;
$$;

CREATE OR REPLACE PROCEDURE core.create_cortex_search()
    RETURNS string
    LANGUAGE sql
    AS $$
BEGIN
        EXECUTE IMMEDIATE 'CREATE CORTEX SEARCH SERVICE movies.data.movies_recommendation_service
        ON CHUNK
        WAREHOUSE = wh_nac
        TARGET_LAG = \'1 hour\'
        AS (
            SELECT *
            FROM movies.data.movies_metadata_chunked
    )';
GRANT ALL ON CORTEX SEARCH SERVICE MOVIES.DATA.MOVIES_RECOMMENDATION_SERVICE TO application role app_public;
RETURN 'Search Created go to Streamlit App';
END;
$$;

-- 4. Grant appropriate privileges over these objects to your application roles.
GRANT USAGE ON PROCEDURE core.table_chunker() TO APPLICATION ROLE app_public;
GRANT USAGE ON PROCEDURE core.create_cortex_search() TO APPLICATION ROLE app_public;


-- 5. Create a streamlit object using the code you wrote in you wrote in src/module-ui, as shown below.
-- The `from` value is derived from the stage path described in snowflake.yml
CREATE OR REPLACE STREAMLIT core.ui
     FROM '/'
     MAIN_FILE = 'ui.py';

-- 6. Grant appropriate privileges over these objects to your application roles.
GRANT USAGE ON STREAMLIT core.ui TO APPLICATION ROLE app_public;

-- A detailed explanation can be found at https://docs.snowflake.com/en/developer-guide/native-apps/adding-streamlit
