# (C) Datadog, Inc. 2023-present
# All rights reserved
# Licensed under a 3-clause BSD style license (see LICENSE)

from __future__ import unicode_literals

import logging
import re
from copy import copy
from typing import Dict, List, NamedTuple, TypedDict

import pytest

from datadog_checks.dev.utils import running_on_windows_ci
from datadog_checks.sqlserver import SQLServer

from .common import CHECK_NAME
from .utils import deep_compare, normalize_ids, normalize_indexes_columns

try:
    import pyodbc
except ImportError:
    pyodbc = None


@pytest.fixture
def dbm_instance(instance_docker):
    instance_docker['dbm'] = True
    instance_docker['min_collection_interval'] = 1
    instance_docker['query_metrics'] = {'enabled': False}
    instance_docker['query_activity'] = {'enabled': False}
    instance_docker['procedure_metrics'] = {'enabled': False}
    # set a very small collection interval so the tests go fast
    instance_docker['collect_settings'] = {
        'enabled': True,
        'run_sync': True,
        'collection_interval': 0.1,
    }
    return copy(instance_docker)


@pytest.mark.integration
@pytest.mark.usefixtures('dd_environment')
@pytest.mark.parametrize(
    "expected_columns,available_columns",
    [
        [
            ["name", "value"],
            ["name", "value"],
        ],
        [
            ["name", "value", "some_missing_column"],
            ["name", "value"],
        ],
    ],
)
def test_get_available_settings_columns(dbm_instance, expected_columns, available_columns):
    check = SQLServer(CHECK_NAME, {}, [dbm_instance])
    check.initialize_connection()
    _conn_key_prefix = "dbm-metadata-"
    with check.connection.open_managed_default_connection(key_prefix=_conn_key_prefix):
        with check.connection.get_managed_cursor(key_prefix=_conn_key_prefix) as cursor:
            result_available_columns = check.sql_metadata._get_available_settings_columns(cursor, expected_columns)
            assert result_available_columns == available_columns


@pytest.mark.integration
@pytest.mark.usefixtures('dd_environment')
def test_get_settings_query_cached(dbm_instance, caplog):
    caplog.set_level(logging.DEBUG)
    check = SQLServer(CHECK_NAME, {}, [dbm_instance])
    check.initialize_connection()
    _conn_key_prefix = "dbm-metadata"
    with check.connection.open_managed_default_connection(key_prefix=_conn_key_prefix):
        with check.connection.get_managed_cursor(key_prefix=_conn_key_prefix) as cursor:
            for _ in range(3):
                query = check.sql_metadata._get_settings_query_cached(cursor)
                assert query, "query should be non-empty"
    times_columns_loaded = 0
    for r in caplog.records:
        if r.message.startswith("found available sys.configurations columns"):
            times_columns_loaded += 1
    assert times_columns_loaded == 1, "columns should have been loaded only once"


def test_sqlserver_collect_settings(aggregator, dd_run_check, dbm_instance):
    check = SQLServer(CHECK_NAME, {}, [dbm_instance])
    # dd_run_check(check)
    check.initialize_connection()
    check.check(dbm_instance)
    dbm_metadata = aggregator.get_event_platform_events("dbm-metadata")
    event = next((e for e in dbm_metadata if e['kind'] == 'sqlserver_configs'), None)
    assert event is not None
    assert event['dbms'] == "sqlserver"
    assert event['kind'] == "sqlserver_configs"
    assert len(event["metadata"]) > 0


def test_collect_schemas(aggregator, dd_run_check, dbm_instance):
    databases_to_find = ['datadog_test_schemas', 'datadog_test_schemas_second']
    exp_datadog_test = {
        'id': 'normalized_value',
        'name': 'datadog_test_schemas_second',
        "collation": "SQL_Latin1_General_CP1_CI_AS",
        'owner': 'dbo',
        'schemas': [
            {
                'name': 'dbo',
                'id': 'normalized_value',
                'owner_name': 'dbo',
                'tables': [
                    {
                        'id': 'normalized_value',
                        'name': 'ϑings',
                        'columns': [
                            {
                                'name': 'id',
                                'data_type': 'int',
                                'default': '((0))',
                                'nullable': True,
                                'ordinal_position': '1',
                            },
                            {
                                'name': 'name',
                                'data_type': 'varchar',
                                'default': 'None',
                                'nullable': True,
                                'ordinal_position': '2',
                            },
                        ],
                        'partitions': {'partition_count': 1},
                        'indexes': [
                            {
                                'name': 'thingsindex',
                                'type': 1,
                                'is_unique': False,
                                'is_primary_key': False,
                                'is_unique_constraint': False,
                                'is_disabled': False,
                                'column_names': 'name',
                            }
                        ],
                    }
                ],
            }
        ],
    }
    exp_datadog_test_schemas = {
        'id': 'normalized_value',
        'name': 'datadog_test_schemas',
        "collation": "SQL_Latin1_General_CP1_CI_AS",
        'owner': 'dbo',
        'schemas': [
            {
                'name': 'test_schema',
                'id': 'normalized_value',
                'owner_name': 'dbo',
                'tables': [
                    {
                        'id': 'normalized_value',
                        'name': 'cities',
                        'columns': [
                            {
                                'name': 'id',
                                'data_type': 'int',
                                'default': '((0))',
                                'nullable': False,
                                'ordinal_position': '1',
                            },
                            {
                                'name': 'name',
                                'data_type': 'varchar',
                                'default': 'None',
                                'nullable': True,
                                'ordinal_position': '2',
                            },
                            {
                                'name': 'population',
                                'data_type': 'int',
                                'default': '((0))',
                                'nullable': False,
                                'ordinal_position': '3',
                            },
                        ],
                        'partitions': {'partition_count': 12},
                        'indexes': [
                            {
                                'name': 'PK_Cities',
                                'type': 1,
                                'is_unique': True,
                                'is_primary_key': True,
                                'is_unique_constraint': False,
                                'is_disabled': False,
                                'column_names': 'id',
                            },
                            {
                                'name': 'single_column_index',
                                'type': 2,
                                'is_unique': False,
                                'is_primary_key': False,
                                'is_unique_constraint': False,
                                'is_disabled': False,
                                'column_names': 'id,population',
                            },
                            {
                                'name': 'two_columns_index',
                                'type': 2,
                                'is_unique': False,
                                'is_primary_key': False,
                                'is_unique_constraint': False,
                                'is_disabled': False,
                                'column_names': 'id,name',
                            },
                        ],
                    },
                    {
                        'id': 'normalized_value',
                        'name': 'landmarks',
                        'columns': [
                            {
                                'name': 'name',
                                'data_type': 'varchar',
                                'default': 'None',
                                'nullable': True,
                                'ordinal_position': '1',
                            },
                            {
                                'name': 'city_id',
                                'data_type': 'int',
                                'default': '((0))',
                                'nullable': True,
                                'ordinal_position': '2',
                            },
                        ],
                        'partitions': {'partition_count': 1},
                        'foreign_keys': [
                            {
                                'foreign_key_name': 'FK_CityId',
                                'referencing_table': 'landmarks',
                                'referencing_column': 'city_id',
                                'referenced_table': 'cities',
                                'referenced_column': 'id',
                            }
                        ],
                    },
                    {
                        'id': 'normalized_value',
                        'name': 'RestaurantReviews',
                        'columns': [
                            {
                                'name': 'RestaurantName',
                                'data_type': 'varchar',
                                'default': 'None',
                                'nullable': True,
                                'ordinal_position': '1',
                            },
                            {
                                'name': 'District',
                                'data_type': 'varchar',
                                'default': 'None',
                                'nullable': True,
                                'ordinal_position': '2',
                            },
                            {
                                'name': 'Review',
                                'data_type': 'varchar',
                                'default': 'None',
                                'nullable': True,
                                'ordinal_position': '3',
                            },
                        ],
                        'partitions': {'partition_count': 1},
                        'foreign_keys': [
                            {
                                'foreign_key_name': 'FK_RestaurantNameDistrict',
                                'referencing_table': 'RestaurantReviews',
                                'referencing_column': 'RestaurantName,District',
                                'referenced_table': 'Restaurants',
                                'referenced_column': 'RestaurantName,District',
                            }
                        ],
                    },
                    {
                        'id': 'normalized_value',
                        'name': 'Restaurants',
                        'columns': [
                            {
                                'name': 'RestaurantName',
                                'data_type': 'varchar',
                                'default': 'None',
                                'nullable': True,
                                'ordinal_position': '1',
                            },
                            {
                                'name': 'District',
                                'data_type': 'varchar',
                                'default': 'None',
                                'nullable': True,
                                'ordinal_position': '2',
                            },
                            {
                                'name': 'Cuisine',
                                'data_type': 'varchar',
                                'default': 'None',
                                'nullable': True,
                                'ordinal_position': '3',
                            },
                        ],
                        'partitions': {'partition_count': 2},
                        'indexes': [
                            {
                                'name': 'UC_RestaurantNameDistrict',
                                'type': 2,
                                'is_unique': True,
                                'is_primary_key': False,
                                'is_unique_constraint': True,
                                'is_disabled': False,
                                'column_names': 'District,RestaurantName',
                            }
                        ],
                    },
                ],
            }
        ],
    }

    if running_on_windows_ci():
        exp_datadog_test['owner'] = 'None'
        exp_datadog_test_schemas['owner'] = 'None'

    expected_data_for_db = {
        'datadog_test_schemas_second': exp_datadog_test,
        'datadog_test_schemas': exp_datadog_test_schemas,
    }

    dbm_instance['database_autodiscovery'] = True
    dbm_instance['autodiscovery_include'] = ['datadog_test_schemas', 'datadog_test_schemas_second']
    dbm_instance['dbm'] = True
    dbm_instance['schemas_collection'] = {"enabled": True, "exclude_tables": ["Chef%"]}

    check = SQLServer(CHECK_NAME, {}, [dbm_instance])
    dd_run_check(check)

    dbm_metadata = aggregator.get_event_platform_events("dbm-metadata")

    actual_payloads = {}

    for schema_event in (e for e in dbm_metadata if e['kind'] == 'sqlserver_databases'):
        assert schema_event.get("timestamp") is not None
        assert schema_event["host"] == "stubbed.hostname"
        assert schema_event["agent_version"] == "0.0.0"
        assert schema_event["dbms"] == "sqlserver"
        assert schema_event.get("collection_interval") is not None
        assert schema_event.get("dbms_version") is not None

        database_metadata = schema_event['metadata']
        assert len(database_metadata) == 1
        db_name = database_metadata[0]['name']

        if db_name in actual_payloads:
            actual_payloads[db_name]['schemas'] = actual_payloads[db_name]['schemas'] + database_metadata[0]['schemas']
        else:
            actual_payloads[db_name] = database_metadata[0]

    assert len(actual_payloads) == len(expected_data_for_db)

    for db_name, actual_payload in actual_payloads.items():

        assert db_name in databases_to_find
        # id's are env dependant
        normalize_ids(actual_payload)
        # index columns may be in any order
        normalize_indexes_columns(actual_payload)
        assert deep_compare(actual_payload, expected_data_for_db[db_name])

def test_collect_schemas_filters(aggregator, dd_run_check, dbm_instance):
    # Test cases are: [config, expected_included_tables, expected_excluded_tables]dDict
    class SchemaFilterTestCase(TypedDict):
        filters: dict
        included: List[str]
        excluded: List[str]

    test_cases: Dict[str, SchemaFilterTestCase] = {
        "test_case_1": {
            "config": {'include_databases': ['.*'], 'include_tables': ['%']},
            "included": [
                'cities', 
                'landmarks', 
                'RestaurantReviews', 
                'Restaurants',
                'Chef1',
                'Chef2',
                'Chef3',
                'Chef4',
            ],
            "excluded": [],
        },
        "test_case_2": {
            "config": {'exclude_tables': ['Restaurant%']},
            "included": [
                'cities', 
                'landmarks',
                'Chef1',
                'Chef2',
                'Chef3',
                'Chef4',
            ],
            "excluded": [
                'RestaurantReviews', 
                'Restaurants',
            ],
        },
        "test_case_3": {
            "config": {'include_tables': ['Restaurant%'], 'exclude_tables': ['Restaurant%']},
            "included": [],
            "excluded": [
                'cities', 
                'landmarks',
                'RestaurantReviews', 
                'Restaurants',
                'Chef1',
                'Chef2',
                'Chef3',
                'Chef4',
            ],
        },
        "test_case_4": {
            "config": {'include_tables': ['Restaurant%', "cities"]},
            "included": [
                'RestaurantReviews', 
                'Restaurants',
                'cities',
            ],
            "excluded": [
                "landmarks",
                'Chef1',
                'Chef2',
                'Chef3',
                'Chef4',
            ],
        },
        "test_case_5": {
            "config": {'exclude_tables': ['Chef%', "cities"]},
            "included": [
                "landmarks",
                'RestaurantReviews', 
                'Restaurants',
            ],
            "excluded": [
                'cities',
                'Chef1',
                'Chef2',
                'Chef3',
                'Chef4',
            ],
        },
        "test_case_6": {
            "config": {'include_tables': ['Chef[2-4]', "cities", "Restaurant%"], 'exclude_tables': ['Chef3', "landmarks"]},
            "included": [
                "cities",
                "Chef2",
                "Chef4",
                'RestaurantReviews', 
                'Restaurants',
            ],
            "excluded": [
                "Chef1",
                "Chef3",
                "landmarks",
            ],
        },
        "test_case_7": {
            "config": {'include_databases': ['.*'], 'exclude_schemas': ['test_.*'], 'include_tables': ['%']},
            "included": [],
            "excluded": [
                'cities', 
                'landmarks', 
                'RestaurantReviews', 
                'Restaurants',
                'Chef1',
                'Chef2',
                'Chef3',
                'Chef4',
            ],
        },
    }


    dbm_instance['database_autodiscovery'] = True
    dbm_instance['autodiscovery_include'] = ['datadog_test_schemas', 'datadog_test_schemas_second']
    dbm_instance['dbm'] = True
    # dbm_instance['schemas_collection'] = {"enabled": True}

    # dbm_metadata = aggregator.get_event_platform_events("dbm-metadata")
    

    i = 0
    for tc in test_cases.values():
        i += 1
        exclude_schemas = tc["config"].get("exclude_schemas", [])
        exclude_schemas.append("dbo")
        schemas_config =  {"enabled": True, "exclude_schemas":exclude_schemas}
        schemas_config.update(tc["config"])
        dbm_instance['schemas_collection'] =  schemas_config

        check = SQLServer(CHECK_NAME, {}, [dbm_instance])
        dd_run_check(check)

        dbm_metadata = aggregator.get_event_platform_events("dbm-metadata")


        tables_got = []
        # Check that all of `include_tables` is in the return and none of `exclude_tables`
        for schema_event in (e for e in dbm_metadata if e['kind'] == 'sqlserver_databases'):    
            assert schema_event.get("timestamp") is not None
            assert schema_event["host"] == "stubbed.hostname"
            assert schema_event["agent_version"] == "0.0.0"
            assert schema_event["dbms"] == "sqlserver"
            assert schema_event.get("collection_interval") is not None
            assert schema_event.get("dbms_version") is not None

            database_metadata = schema_event['metadata']
            assert len(database_metadata) == 1
            db_name = database_metadata[0]['name']

            schema = database_metadata[0]['schemas'][0]
            schema_name = schema['name']
            assert schema_name in ['test_schema']

            for table in schema['tables']:
                tables_got.append(table['name'])

        for table in tables_got:
            assert_fields(tables_got, tc["included"])
            assert_not_fields(tables_got, tc["excluded"])

        aggregator.reset()

def test_get_table_filter(dbm_instance):
    test_cases = [
        [{'include_tables': ['cats']}, " AND (name LIKE 'cats')"],
        [{'exclude_tables': ['dogs']}, " AND NOT (name LIKE 'dogs')"],
        [
            {'include_tables': ['cats', "'people'"], 'exclude_tables': ['dogs', 'iguanas']},
            " AND (name LIKE 'cats' OR name LIKE '''people''')"
            " AND NOT (name LIKE 'dogs' OR name LIKE 'iguanas')",
        ]
    ]

    for tc in test_cases:
        dbm_instance['schemas_collection'] = tc[0]
        check = SQLServer(CHECK_NAME, {}, [dbm_instance])
        

        metadata = check._schemas
        filter = metadata._get_tables_filter()
        
        assert filter == tc[1]

# def test_should_collect_metadata(integration_check, dbm_instance):
#     test_cases = [
#         [{'include_databases': ['d%']}, "db", "database", True],
#         [{'include_databases': ['d%']}, "db", "database", True],
#         [{'include_databases': ['c%'], 'include_schemas': ['d%']}, "db", "database", False],
#         [{'include_databases': ['d%'], 'exclude_schemas': ['d%']}, "db", "database", True],
#         [{'exclude_databases': ['c%']}, "db", "database", True],
#         [{'exclude_databases': ['d%']}, "db", "database", False],
#         [{'include_databases': ['d%'], 'exclude_databases': ['c%']}, "db", "database", True],
#         [{'include_databases': ['c%'], 'exclude_databases': ['c%']}, "db", "database", False],
#         [{'include_databases': ['d%'], 'exclude_databases': ['b$']}, "db", "database", False],
#         [{'include_databases': ['c%']}, "sch", "schema", True],
#         [{'exclude_databases': ['sc%']}, "sch", "schema", True],
#         [{'include_schemas': ['p%']}, "public", "schema", True],
#         [{'include_schemas': ['x%']}, "public", "schema", False],
#         [{'exclude_schemas': ['z%']}, "public", "schema", True],
#         [{'exclude_schemas': ['l%']}, "public", "schema", False],
#         [{'include_schemas': ['p%'], 'exclude_schemas': ['z%']}, "public", "schema", True],
#         [{'include_schemas': ['z%'], 'exclude_schemas': ['c$']}, "public", "schema", False],
#         [{'include_schemas': ['p%'], 'exclude_schemas': ['b%']}, "public", "schema", False],
#     ]
#     for tc in test_cases:
#         dbm_instance['schemas_collection'] = tc[0]
#         check = SQLServer(CHECK_NAME, {}, [dbm_instance])
#         dd_run_check(check)
#         metadata = check.metadata_samples

#         assert metadata._should_collect_metadata(tc[1], tc[2]) == tc[3], tc


@pytest.mark.flaky
def test_schemas_collection_truncated(aggregator, dd_run_check, dbm_instance):
    dbm_instance['database_autodiscovery'] = True
    dbm_instance['autodiscovery_include'] = ['datadog_test_schemas']
    dbm_instance['dbm'] = True
    dbm_instance['schemas_collection'] = {"enabled": True, "max_execution_time": 0}
    expected_pattern = r"^Truncated after fetching \d+ columns, elapsed time is \d+(\.\d+)?s, database is .*"
    check = SQLServer(CHECK_NAME, {}, [dbm_instance])
    dd_run_check(check)
    dbm_metadata = aggregator.get_event_platform_events("dbm-metadata")
    found = False
    for schema_event in (e for e in dbm_metadata if e['kind'] == 'sqlserver_databases'):
        if "collection_errors" in schema_event:
            if schema_event["collection_errors"][0]["error_type"] == "truncated" and re.fullmatch(
                expected_pattern, schema_event["collection_errors"][0]["message"]
            ):
                found = True
    assert found

def assert_fields(keys: List[str], fields: List[str]):
    for field in fields:
        assert field in keys
def assert_not_fields(keys: List[str], fields: List[str]):
    for field in fields:
        assert field not in keys