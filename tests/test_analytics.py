from pathlib import Path

import duckdb
import pytest

from app.analytics import AnalyticsJoin, AnalyticsJoinQuery, AnalyticsMetric, AnalyticsOrder, AnalyticsQuery, ParquetAnalytics


def _create_sales_file(directory: Path) -> None:
    path = directory / "sales.parquet"
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            """
            COPY (
                SELECT * FROM (VALUES
                    ('west', 10.0),
                    ('west', 20.0),
                    ('east', 5.0)
                ) AS sales(region, amount)
            ) TO ? (FORMAT parquet)
            """,
            [str(path)],
        )


def _create_claims_files(directory: Path) -> None:
    with duckdb.connect(database=":memory:") as connection:
        connection.execute(
            """
            COPY (
                SELECT * FROM (VALUES
                    ('clm_1', 'pat_1', 'npi_1', 100.0),
                    ('clm_2', 'pat_1', 'npi_2', 50.0),
                    ('clm_3', 'pat_2', 'npi_1', 25.0)
                ) AS claims(claim_id, patient_id, provider_npi, total_paid)
            ) TO ? (FORMAT parquet)
            """,
            [str(directory / "claims.parquet")],
        )
        connection.execute(
            """
            COPY (
                SELECT * FROM (VALUES
                    ('pat_1', 'F'),
                    ('pat_2', 'M')
                ) AS patients(patient_id, gender)
            ) TO ? (FORMAT parquet)
            """,
            [str(directory / "patients.parquet")],
        )
        connection.execute(
            """
            COPY (
                SELECT * FROM (VALUES
                    ('npi_1', 'Cardiology'),
                    ('npi_2', 'Primary Care')
                ) AS providers(provider_npi, specialty)
            ) TO ? (FORMAT parquet)
            """,
            [str(directory / "providers.parquet")],
        )


def test_lists_and_describes_parquet_files(tmp_path: Path) -> None:
    _create_sales_file(tmp_path)
    analytics = ParquetAnalytics(tmp_path)

    assert [item["name"] for item in analytics.list_datasets()] == ["sales.parquet"]
    assert {item["column_name"] for item in analytics.schema("sales.parquet")} >= {"region", "amount"}


def test_grouped_query_uses_safe_structured_metrics(tmp_path: Path) -> None:
    _create_sales_file(tmp_path)
    analytics = ParquetAnalytics(tmp_path)
    request = AnalyticsQuery(
        dataset="sales.parquet",
        group_by=["region"],
        metrics=[AnalyticsMetric(function="avg", column="amount")],
        limit=10,
    )

    result = analytics.query(request)
    rows = {row["region"]: row["avg_amount"] for row in result["rows"]}

    assert rows == {"west": 15.0, "east": 5.0}


def test_dataset_path_must_come_from_inventory(tmp_path: Path) -> None:
    analytics = ParquetAnalytics(tmp_path)

    with pytest.raises(ValueError, match="Unknown dataset"):
        analytics.schema("../secret.parquet")


def test_join_query_groups_metrics_across_parquet_files(tmp_path: Path) -> None:
    _create_claims_files(tmp_path)
    analytics = ParquetAnalytics(tmp_path)
    request = AnalyticsJoinQuery(
        base_dataset="claims.parquet",
        joins=[
            AnalyticsJoin(dataset="providers.parquet", left_column="claims.provider_npi", right_column="provider_npi"),
        ],
        group_by=["providers.specialty"],
        metrics=[AnalyticsMetric(function="sum", column="claims.total_paid")],
        order_by=[AnalyticsOrder(column="sum_claims_total_paid", direction="desc")],
        limit=10,
    )

    result = analytics.join_query(request)
    rows = {row["providers_specialty"]: row["sum_claims_total_paid"] for row in result["rows"]}

    assert rows == {"Cardiology": 125.0, "Primary Care": 50.0}
    assert "JOIN read_parquet" in result["sql_preview"]


def test_join_query_can_preview_sql_without_running(tmp_path: Path) -> None:
    _create_claims_files(tmp_path)
    analytics = ParquetAnalytics(tmp_path)
    request = AnalyticsJoinQuery(
        base_dataset="claims.parquet",
        joins=[AnalyticsJoin(dataset="patients.parquet", left_column="patient_id", right_column="patient_id")],
        group_by=["patients.gender"],
        metrics=[AnalyticsMetric(function="count")],
        dry_run=True,
    )

    result = analytics.join_query(request)

    assert result["dry_run"] is True
    assert result["rows"] == []
    assert '"claims"."patient_id" = "patients"."patient_id"' in result["sql_preview"]


def test_join_query_rejects_ambiguous_columns(tmp_path: Path) -> None:
    _create_claims_files(tmp_path)
    analytics = ParquetAnalytics(tmp_path)
    request = AnalyticsJoinQuery(
        base_dataset="claims.parquet",
        joins=[AnalyticsJoin(dataset="patients.parquet", left_column="patient_id", right_column="patient_id")],
        columns=["patient_id"],
    )

    with pytest.raises(ValueError, match="Ambiguous column"):
        analytics.join_query(request)
