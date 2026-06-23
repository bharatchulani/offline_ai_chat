from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import duckdb
from pydantic import BaseModel, Field, model_validator


FilterOperator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "is_null", "not_null"]
Aggregate = Literal["count", "sum", "avg", "min", "max", "stddev"]
Direction = Literal["asc", "desc"]
JoinType = Literal["inner", "left"]


class AnalyticsFilter(BaseModel):
    column: str = Field(min_length=1)
    operator: FilterOperator
    value: Any | None = None

    @model_validator(mode="after")
    def validate_value(self) -> "AnalyticsFilter":
        if self.operator not in {"is_null", "not_null"} and self.value is None:
            raise ValueError(f"Filter operator '{self.operator}' requires a value")
        return self


class AnalyticsMetric(BaseModel):
    function: Aggregate = Field(description="Aggregation to calculate. Use count for row/claim counts, sum for totals.")
    column: str | None = Field(
        default=None,
        description=(
            "Column to aggregate. For joins, prefer qualified names like claims.total_paid. "
            "Do not put numeric measure columns such as total_paid in group_by; aggregate them with sum/avg/min/max."
        ),
    )

    @model_validator(mode="after")
    def validate_column(self) -> "AnalyticsMetric":
        if self.function != "count" and not self.column:
            raise ValueError(f"Metric '{self.function}' requires a column")
        return self


class AnalyticsOrder(BaseModel):
    column: str = Field(min_length=1)
    direction: Direction = "asc"


class AnalyticsQuery(BaseModel):
    dataset: str = Field(description="A relative .parquet filename from /analytics/datasets, or * for all files")
    columns: list[str] = Field(default_factory=list, description="Non-aggregated columns to return")
    filters: list[AnalyticsFilter] = Field(default_factory=list)
    group_by: list[str] = Field(
        default_factory=list,
        description="Dimension/categorical columns to group by. Do not group by numeric measures that should be summed or averaged.",
    )
    metrics: list[AnalyticsMetric] = Field(default_factory=list, description="Aggregations for numeric measures and counts")
    order_by: list[AnalyticsOrder] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=1000)


class AnalyticsJoin(BaseModel):
    dataset: str = Field(description="Dataset to join to the base dataset")
    left_column: str = Field(description="Column on the already-joined left side, preferably qualified, e.g. claims.provider_npi")
    right_column: str = Field(description="Column on this join dataset, optionally qualified, e.g. provider_npi")
    join_type: JoinType = "inner"


class AnalyticsJoinQuery(BaseModel):
    base_dataset: str = Field(description="Starting .parquet dataset from /analytics/datasets")
    joins: list[AnalyticsJoin] = Field(default_factory=list)
    columns: list[str] = Field(
        default_factory=list,
        description="Non-aggregated columns to select, preferably qualified as dataset.column. Use only with group_by when metrics are present.",
    )
    filters: list[AnalyticsFilter] = Field(default_factory=list)
    group_by: list[str] = Field(
        default_factory=list,
        description=(
            "Dimension/categorical columns to group by, preferably qualified as dataset.column. "
            "Examples: providers.provider_specialty, patients.sex, diagnosis_xwalk.diagnosis_desc. "
            "Do not group by total_paid; use metrics=[{function:'sum', column:'claims.total_paid'}]."
        ),
    )
    metrics: list[AnalyticsMetric] = Field(
        default_factory=list,
        description=(
            "Aggregations to calculate. Example for total paid: "
            "{function:'sum', column:'claims.total_paid'}. Example for claim count: {function:'count', column:'claims.claim_id'}."
        ),
    )
    order_by: list[AnalyticsOrder] = Field(default_factory=list)
    limit: int = Field(default=100, ge=1, le=1000)
    dry_run: bool = Field(default=False, description="Return the SQL preview without executing the query")


class DatasetRequest(BaseModel):
    dataset: str = Field(description="A relative .parquet filename from /analytics/datasets, or * for all files")


@dataclass(frozen=True)
class Dataset:
    name: str
    path: Path
    size_bytes: int
    modified_utc: str


def _identifier(value: str) -> str:
    return f'"{value.replace(chr(34), chr(34) * 2)}"'


def _alias(dataset: str) -> str:
    return Path(dataset).stem.replace("-", "_").replace(" ", "_")


def _output_alias(column: str) -> str:
    return column.replace(".", "_")


def _resolve_column_name(name: str, available: set[str], dataset_alias: str) -> str:
    """Resolve exact columns plus safe singular shorthand like providers.specialty -> provider_specialty."""
    if name in available:
        return name
    alias_prefix = dataset_alias.removesuffix("s")
    candidates = [
        column for column in available
        if column.endswith(f"_{name}") or column == f"{alias_prefix}_{name}"
    ]
    if len(candidates) == 1:
        return candidates[0]
    return name


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return str(value)


class ParquetAnalytics:
    def __init__(self, root: Path, max_rows: int = 1000) -> None:
        self.root = root.resolve()
        self.max_rows = max_rows
        self.root.mkdir(parents=True, exist_ok=True)

    def list_datasets(self) -> list[dict[str, Any]]:
        datasets = []
        for path in sorted(self.root.rglob("*.parquet")):
            if not path.is_file():
                continue
            stat = path.stat()
            datasets.append(
                {
                    "name": path.relative_to(self.root).as_posix(),
                    "size_bytes": stat.st_size,
                    "modified_utc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                }
            )
        return datasets

    def schema(self, dataset: str) -> list[dict[str, Any]]:
        paths = self._paths(dataset)
        query = "DESCRIBE SELECT * FROM read_parquet(?, union_by_name=true, filename=true)"
        return self._records(query, [paths])

    def summarize(self, dataset: str) -> list[dict[str, Any]]:
        paths = self._paths(dataset)
        query = "SUMMARIZE SELECT * FROM read_parquet(?, union_by_name=true, filename=true)"
        return self._records(query, [paths])

    def query(self, request: AnalyticsQuery) -> dict[str, Any]:
        paths = self._paths(request.dataset)
        available = {item["column_name"] for item in self.schema(request.dataset)}
        self._validate_columns(request, available)

        selected: list[str] = []
        output_names: list[str] = []
        for column in request.group_by:
            if column not in output_names:
                selected.append(_identifier(column))
                output_names.append(column)
        for column in request.columns:
            if column not in output_names:
                selected.append(_identifier(column))
                output_names.append(column)

        for index, metric in enumerate(request.metrics, start=1):
            target = "*" if metric.column is None else _identifier(metric.column)
            alias_base = f"{metric.function}_{metric.column or 'rows'}"
            alias = alias_base if alias_base not in output_names else f"{alias_base}_{index}"
            selected.append(f"{metric.function.upper()}({target}) AS {_identifier(alias)}")
            output_names.append(alias)

        if not selected:
            selected = ["*"]
            output_names = list(available)

        parameters: list[Any] = [paths]
        predicates: list[str] = []
        operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        for item in request.filters:
            column = _identifier(item.column)
            if item.operator in operators:
                predicates.append(f"{column} {operators[item.operator]} ?")
                parameters.append(item.value)
            elif item.operator == "contains":
                predicates.append(f"CAST({column} AS VARCHAR) ILIKE ?")
                parameters.append(f"%{item.value}%")
            elif item.operator == "is_null":
                predicates.append(f"{column} IS NULL")
            else:
                predicates.append(f"{column} IS NOT NULL")

        sql = (
            f"SELECT {', '.join(selected)} "
            "FROM read_parquet(?, union_by_name=true, filename=true)"
        )
        if predicates:
            sql += " WHERE " + " AND ".join(predicates)
        if request.group_by:
            sql += " GROUP BY " + ", ".join(_identifier(column) for column in request.group_by)
        if request.order_by:
            sql += " ORDER BY " + ", ".join(
                f"{_identifier(item.column)} {item.direction.upper()}" for item in request.order_by
            )
        effective_limit = min(request.limit, self.max_rows)
        sql += f" LIMIT {effective_limit}"

        records = self._records(sql, parameters)
        return {"columns": list(records[0]) if records else output_names, "rows": records, "row_count": len(records)}

    def join_query(self, request: AnalyticsJoinQuery) -> dict[str, Any]:
        sql, parameters, output_names = self._build_join_sql(request)
        if request.dry_run:
            return {"sql_preview": sql, "columns": output_names, "rows": [], "row_count": 0, "dry_run": True}
        records = self._records(sql, parameters)
        return {
            "sql_preview": sql,
            "columns": list(records[0]) if records else output_names,
            "rows": records,
            "row_count": len(records),
        }

    def _build_join_sql(self, request: AnalyticsJoinQuery) -> tuple[str, list[Any], list[str]]:
        dataset_names = [request.base_dataset] + [item.dataset for item in request.joins]
        if len(dataset_names) != len(set(dataset_names)):
            raise ValueError("Each dataset may appear only once in a join query")

        aliases = {dataset: _alias(dataset) for dataset in dataset_names}
        if len(set(aliases.values())) != len(aliases):
            raise ValueError("Dataset aliases are ambiguous; use uniquely named parquet files")

        schemas = {dataset: {item["column_name"] for item in self.schema(dataset)} for dataset in dataset_names}
        alias_to_dataset = {alias: dataset for dataset, alias in aliases.items()}

        parameters: list[Any] = [self._paths(request.base_dataset)]
        base_alias = aliases[request.base_dataset]
        sql = f"FROM read_parquet(?, union_by_name=true, filename=true) AS {_identifier(base_alias)}"
        joined_aliases = {base_alias}

        for join in request.joins:
            join_alias = aliases[join.dataset]
            left = self._qualified(join.left_column, schemas, alias_to_dataset, joined_aliases)
            right = self._qualified(join.right_column, schemas, alias_to_dataset, {join_alias}, default_alias=join_alias)
            parameters.append(self._paths(join.dataset))
            sql += (
                f" {join.join_type.upper()} JOIN read_parquet(?, union_by_name=true, filename=true) "
                f"AS {_identifier(join_alias)} ON {left} = {right}"
            )
            joined_aliases.add(join_alias)

        self._validate_join_request(request, schemas, alias_to_dataset, joined_aliases)

        selected: list[str] = []
        output_names: list[str] = []
        for column in request.group_by:
            alias = _output_alias(column)
            selected.append(f"{self._qualified(column, schemas, alias_to_dataset, joined_aliases)} AS {_identifier(alias)}")
            output_names.append(alias)
        for column in request.columns:
            alias = _output_alias(column)
            if alias not in output_names:
                selected.append(f"{self._qualified(column, schemas, alias_to_dataset, joined_aliases)} AS {_identifier(alias)}")
                output_names.append(alias)

        for index, metric in enumerate(request.metrics, start=1):
            if metric.column is None:
                target = "*"
                alias_base = "count_rows"
            else:
                target = self._qualified(metric.column, schemas, alias_to_dataset, joined_aliases)
                alias_base = f"{metric.function}_{_output_alias(metric.column)}"
            alias = alias_base if alias_base not in output_names else f"{alias_base}_{index}"
            selected.append(f"{metric.function.upper()}({target}) AS {_identifier(alias)}")
            output_names.append(alias)

        if not selected:
            selected = [f"{_identifier(base_alias)}.*"]
            output_names = sorted(schemas[request.base_dataset])

        predicates: list[str] = []
        operators = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
        for item in request.filters:
            column = self._qualified(item.column, schemas, alias_to_dataset, joined_aliases)
            if item.operator in operators:
                predicates.append(f"{column} {operators[item.operator]} ?")
                parameters.append(item.value)
            elif item.operator == "contains":
                predicates.append(f"CAST({column} AS VARCHAR) ILIKE ?")
                parameters.append(f"%{item.value}%")
            elif item.operator == "is_null":
                predicates.append(f"{column} IS NULL")
            else:
                predicates.append(f"{column} IS NOT NULL")

        full_sql = f"SELECT {', '.join(selected)} {sql}"
        if predicates:
            full_sql += " WHERE " + " AND ".join(predicates)
        if request.group_by:
            full_sql += " GROUP BY " + ", ".join(
                self._qualified(column, schemas, alias_to_dataset, joined_aliases) for column in request.group_by
            )
        if request.order_by:
            full_sql += " ORDER BY " + ", ".join(
                f"{_identifier(item.column)} {item.direction.upper()}" for item in request.order_by
            )
        full_sql += f" LIMIT {min(request.limit, self.max_rows)}"
        return full_sql, parameters, output_names

    def _paths(self, dataset: str) -> list[str]:
        files = [item["name"] for item in self.list_datasets()]
        if dataset == "*":
            selected = files
        elif dataset in files:
            selected = [dataset]
        else:
            raise ValueError(f"Unknown dataset '{dataset}'. Use /analytics/datasets to list valid names.")
        if not selected:
            raise ValueError("No Parquet files are available")
        return [str((self.root / name).resolve()) for name in selected]

    def _validate_columns(self, request: AnalyticsQuery, available: set[str]) -> None:
        requested = set(request.columns) | set(request.group_by)
        requested |= {item.column for item in request.filters}
        requested |= {item.column for item in request.metrics if item.column}
        missing = requested - available
        if missing:
            raise ValueError(f"Unknown columns: {sorted(missing)}")
        if request.metrics and any(column not in request.group_by for column in request.columns):
            raise ValueError("When metrics are used, selected columns must also appear in group_by")

        metric_aliases = {f"{item.function}_{item.column or 'rows'}" for item in request.metrics}
        valid_order = set(request.columns) | set(request.group_by) | metric_aliases
        invalid_order = {item.column for item in request.order_by} - valid_order
        if invalid_order:
            raise ValueError(f"Invalid order_by columns: {sorted(invalid_order)}")

    def _validate_join_request(
        self,
        request: AnalyticsJoinQuery,
        schemas: dict[str, set[str]],
        alias_to_dataset: dict[str, str],
        joined_aliases: set[str],
    ) -> None:
        for column in request.columns + request.group_by:
            self._qualified(column, schemas, alias_to_dataset, joined_aliases)
        for item in request.filters:
            self._qualified(item.column, schemas, alias_to_dataset, joined_aliases)
        for item in request.metrics:
            if item.column:
                self._qualified(item.column, schemas, alias_to_dataset, joined_aliases)
        if request.metrics and any(column not in request.group_by for column in request.columns):
            raise ValueError("When metrics are used, selected columns must also appear in group_by")

        metric_aliases = {
            f"{item.function}_{_output_alias(item.column)}" if item.column else "count_rows"
            for item in request.metrics
        }
        valid_order = {_output_alias(column) for column in request.columns + request.group_by} | metric_aliases
        invalid_order = {item.column for item in request.order_by} - valid_order
        if invalid_order:
            raise ValueError(f"Invalid order_by columns: {sorted(invalid_order)}")

    def _qualified(
        self,
        column: str,
        schemas: dict[str, set[str]],
        alias_to_dataset: dict[str, str],
        allowed_aliases: set[str],
        default_alias: str | None = None,
    ) -> str:
        if "." in column:
            alias, name = column.split(".", 1)
            if alias not in alias_to_dataset:
                raise ValueError(f"Unknown dataset alias '{alias}' in column '{column}'")
            if alias not in allowed_aliases:
                raise ValueError(f"Dataset alias '{alias}' is not available at this point in the join")
            resolved = _resolve_column_name(name, schemas[alias_to_dataset[alias]], alias)
            if resolved not in schemas[alias_to_dataset[alias]]:
                raise ValueError(
                    f"Unknown column '{name}' on dataset alias '{alias}'. "
                    f"Available columns: {sorted(schemas[alias_to_dataset[alias]])}"
                )
            return f"{_identifier(alias)}.{_identifier(resolved)}"

        if default_alias:
            resolved = _resolve_column_name(column, schemas[alias_to_dataset[default_alias]], default_alias)
            if resolved not in schemas[alias_to_dataset[default_alias]]:
                raise ValueError(
                    f"Unknown column '{column}' on dataset alias '{default_alias}'. "
                    f"Available columns: {sorted(schemas[alias_to_dataset[default_alias]])}"
                )
            return f"{_identifier(default_alias)}.{_identifier(resolved)}"

        matches = [
            alias for alias in allowed_aliases
            if _resolve_column_name(column, schemas[alias_to_dataset[alias]], alias) in schemas[alias_to_dataset[alias]]
        ]
        if not matches:
            raise ValueError(f"Unknown column '{column}'")
        if len(matches) > 1:
            raise ValueError(f"Ambiguous column '{column}'. Use dataset.column, for example claims.{column}")
        resolved = _resolve_column_name(column, schemas[alias_to_dataset[matches[0]]], matches[0])
        return f"{_identifier(matches[0])}.{_identifier(resolved)}"

    @staticmethod
    def _records(query: str, parameters: list[Any]) -> list[dict[str, Any]]:
        with duckdb.connect(database=":memory:") as connection:
            result = connection.execute(query, parameters)
            columns = [item[0] for item in result.description]
            return [
                {column: _json_value(value) for column, value in zip(columns, row, strict=True)}
                for row in result.fetchall()
            ]
