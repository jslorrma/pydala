import duckdb
import pandas as pd
import polars as pl
import pyarrow as pa
import pyarrow.dataset as ds
import duckdb


def get_ddb_sort_str(sort_by: str | list, ascending: bool | list | None = None) -> str:
    ascending = True if ascending is None else ascending
    if isinstance(sort_by, list):

        if isinstance(ascending, bool):
            ascending = [ascending] * len(sort_by)

        sort_by_ddb = [
            f"{col} ASC" if asc else f"{col} DESC"
            for col, asc in zip(sort_by, ascending)
        ]
        sort_by_ddb = ",".join(sort_by_ddb)

    else:
        sort_by_ddb = sort_by + " ASC" if ascending else sort_by + " DESC"

    return sort_by_ddb


def to_polars(
    table: pa.Table
    | pd.DataFrame
    | pl.DataFrame
    | duckdb.DuckDBPyRelation
    | ds.FileSystemDataset,
) -> pl.DataFrame:

    if isinstance(table, pa.Table):
        pl_dataframe = pl.from_arrow(table)

    elif isinstance(table, pd.DataFrame):
        pl_dataframe = pl.from_pandas(table)

    elif isinstance(table, ds.FileSystemDataset):
        pl_dataframe = pl.from_arrow(table.to_table())

    elif isinstance(table, duckdb.DuckDBPyRelation):
        pl_dataframe = pl.from_arrow(table.arrow())

    else:
        pl_dataframe = table

    return pl_dataframe


def to_pandas(
    table: pa.Table
    | pd.DataFrame
    | pl.DataFrame
    | duckdb.DuckDBPyRelation
    | ds.FileSystemDataset,
) -> pd.DataFrame:

    if isinstance(table, pa.Table):
        pd_dataframe = table.to_pandas()

    elif isinstance(table, pl.DataFrame):
        pd_dataframe = table.to_pandas()

    elif isinstance(table, ds.FileSystemDataset):
        pd_dataframe = table.to_table().to_pandas()

    elif isinstance(table, duckdb.DuckDBPyRelation):
        pd_dataframe = table.df()

    else:
        pd_dataframe = table

    return pd_dataframe


def to_relation(
    table: duckdb.DuckDBPyRelation
    | pa.Table
    | ds.FileSystemDataset
    | pd.DataFrame
    | pl.DataFrame
    | str,
    ddb: duckdb.DuckDBPyConnection,
    sort_by: str | list | None = None,
    ascending: bool | list | None = None,
    distinct: bool = False,
    drop: str | list | None = None,
) -> duckdb.DuckDBPyRelation:

    if isinstance(table, pa.Table):
        if distinct:
            table = distinct_table(table)

        if sort_by is not None:
            table = sort_table(
                drop_columns(table=table, columns=drop),
                sort_by=sort_by,
                ascending=ascending,
            )

        return ddb.from_arrow(table)

    elif isinstance(table, ds.FileSystemDataset):

        table = ddb.from_arrow(table)

        if distinct:
            table = table.distinct()

        if drop is not None:
            table = drop_columns(table, columns=drop)

        if sort_by is not None:
            sort_by = get_ddb_sort_str(sort_by=sort_by, ascending=ascending)
            table = table.order(sort_by)

        return table

    elif isinstance(table, pd.DataFrame):

        if distinct:
            table = distinct_table(table)

        if sort_by is not None:
            table = sort_table(
                drop_columns(table, columns=drop), sort_by=sort_by, ascending=ascending
            )

        return ddb.from_df(table)

    elif isinstance(table, pl.DataFrame):

        if distinct:
            table = distinct_table(table)

        if sort_by is not None:
            table = sort_table(
                drop_columns(table, columns=drop),
                sort_by=sort_by,
                ascending=ascending,
                ddb=ddb,
            )

        return ddb.from_arrow(table.to_arrow())

    elif isinstance(table, str):
        if ".parquet" in table:
            table = ddb.from_parquet(table)
        elif ".csv" in table:
            table = ddb.from_csv_auto(table)
        else:
            table = ddb.query(f"SELECT * FROM '{table}'")

        if distinct:
            table = table.distinct()

        if drop is not None:
            table = drop_columns(table, columns=drop)

        if sort_by is not None:
            sort_by = get_ddb_sort_str(sort_by=sort_by, ascending=ascending)
            table = table.order(sort_by)

        return table

    elif isinstance(table, duckdb.DuckDBPyRelation):
        table = table

        if sort_by is not None:
            sort_by = get_ddb_sort_str(sort_by=sort_by, ascending=ascending)
            table = table.order(sort_by)

        if distinct:
            table = table.distinct()

        return table


def sort_table(
    table: pa.Table
    | pd.DataFrame
    | pl.DataFrame
    | duckdb.DuckDBPyRelation
    | ds.FileSystemDataset,
    sort_by: str | list | tuple | None,
    ascending: bool | list | tuple | None,
    ddb: duckdb.DuckDBPyConnection | None = None,
) -> pa.Table | pd.DataFrame | pl.DataFrame | duckdb.DuckDBPyRelation:

    if sort_by is not None:
        if ascending is None:
            ascending = True

        if isinstance(ascending, bool):
            reverse = not ascending
        else:
            reverse = [not el for el in ascending]

        if isinstance(table, pa.Table):

            return to_polars(table=table).sort(by=sort_by, reverse=reverse).to_arrow()

        elif isinstance(table, pd.DataFrame):
            return to_polars(table=table).sort(by=sort_by, reverse=reverse).to_pandas()

        elif isinstance(table, ds.FileSystemDataset):
            return to_polars(table=table).sort(by=sort_by, reverse=reverse).to_arrow()

        elif isinstance(table, pl.DataFrame):
            return table.sort(by=sort_by, reverse=reverse)

        elif isinstance(table, duckdb.DuckDBPyRelation):
            return ddb.from_arrow(
                to_polars(table).sort(by=sort_by, reverse=reverse).to_arrow()
            )
    else:
        return table


def get_tables_diff(
    table1: pa.Table
    | pd.DataFrame
    | pl.DataFrame
    | duckdb.DuckDBPyRelation
    | ds.FileSystemDataset
    | str,
    table2: pa.Table
    | pd.DataFrame
    | pl.DataFrame
    | duckdb.DuckDBPyRelation
    | ds.FileSystemDataset
    | str,
    ddb: duckdb.DuckDBPyConnection | None = None,
) -> pa.Table | pd.DataFrame | pl.DataFrame | duckdb.DuckDBPyRelation:

    if type(table1) != type(table2):
        raise TypeError

    else:
        if isinstance(table1, pa.Table):
            return ddb.from_arrow(table1).except_(ddb.from_arrow(table2)).arrow()
        elif isinstance(table1, pd.DataFrame):
            return ddb.from_df(table1).except_(ddb.from_df(table2)).df()
        elif isinstance(table1, pl.DataFrame):
            return pl.concat([table1.with_row_count(), table2.with_row_count()]).filter(
                pl.count().over(table1.columns) == 1
            )
        elif isinstance(table1, str):
            return ddb.execute(
                f"SELECT * FROM {table1} EXCEPT SELECT * FROM {table2}"
            ).arrow()


def distinct_table(
    table: pa.Table
    | pd.DataFrame
    | pl.DataFrame
    | duckdb.DuckDBPyRelation
    | ds.FileSystemDataset,
    ddb: duckdb.DuckDBPyConnection | None = None,
) -> pa.Table | pd.DataFrame | pl.DataFrame | duckdb.DuckDBPyRelation:

    if isinstance(table, pa.Table):
        table = to_polars(table=table)
        if not table.is_unique().all():
            return table.unique().to_arrow()
        else:
            return table.to_arrow()

    elif isinstance(table, pd.DataFrame):
        table = to_polars(table=table)
        if not table.is_unique().all():
            return table.unique().to_pandas()
        else:
            return table.to_pandas()

    elif isinstance(table, ds.FileSystemDataset):
        table = to_polars(table=table)
        if not table.is_unique().all():
            return table.unique().to_arrow()
        else:
            return table.to_arrow()

    elif isinstance(table, pl.DataFrame):
        if not table.is_unique().all():
            return table.unique().to_arrow()
        else:
            return table.to_arrow()

    elif isinstance(table, duckdb.DuckDBPyRelation):
        table = to_polars(table=table)
        if not table.is_unique().all():
            return ddb.from_arrow(table.unique().to_arrow())
        else:
            return ddb.from_arrow(table.to_arrow())


def drop_columns(
    table: pa.Table
    | pd.DataFrame
    | pl.DataFrame
    | duckdb.DuckDBPyRelation
    | ds.FileSystemDataset,
    columns: str | list | None = None,
) -> pa.Table | pd.DataFrame | pl.DataFrame | duckdb.DuckDBPyRelation:
    if columns is not None:
        if isinstance(table, (pa.Table, pl.DataFrame, pd.DataFrame)):
            columns = [col for col in columns if col in table.column_names]
            return table.drop(columns=columns)

        elif isinstance(table, ds.FileSystemDataset):
            columns = [col for col in table.schema.names if col not in columns]
            return table.to_table(columns=columns)

        elif isinstance(table, duckdb.DuckDBPyRelation):
            columns = [
                f"'{col}'" if " " in col else col
                for col in table.columns
                if col not in columns
            ]
            return table.project(",".join(columns))
    else:
        return table
