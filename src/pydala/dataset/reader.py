import uuid
import duckdb
import os
import pandas as pd
import polars as pl
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.feather as pf
import pyarrow.parquet as pq
from tempfile import mkdtemp
from ..filesystem import FileSystem, S5CMD
from ..utils import (
    distinct_table,
    get_ddb_sort_str,
    drop_columns,
    sort_table,
    to_pandas,
    to_polars,
    to_relation,
)


class Reader:
    def __init__(
        self,
        path: str,
        bucket: str | None = None,
        name: str | None = None,
        partitioning: ds.Partitioning | str | None = None,
        filesystem: FileSystem,
        format: str | None = "parquet",
        sort_by: str | list | None = None,
        ascending: bool | list | None = None,
        distinct: bool | None = None,
        drop: str | list | None = "__index_level_0__",
        ddb: duckdb.DuckDBPyConnection | None = None,
        caching:bool=False,
        cache_prefix:str|None="/tmp/pydala",
        
    ):
        self._bucket = bucket
        self._name = name

        self._path = os.path.join(bucket, path) if bucket is not None else path
        self._path_org = self._path
            
        self._filesystem = filesystem
        self._format = format
        self._partitioning = partitioning
        self.sort(by=sort_by, ascending=ascending)
        self.distinct(distinct)
        self.drop(columns=drop)

        self._caching = caching
        self._has_local = False
        self._cache_prefix = cache_prefix
        if cache_prefix is not None:
            os.makedirs(cache_prefix, exist_ok=True)
            self._cache_path =  mkdtemp(prefix=cache_prefix)
        else:
            self._cache_path = mkdtemp()
        if ddb is not None:
            self.ddb = ddb
        else:
            self.ddb = duckdb.connect()
        self.ddb.execute(f"SET temp_directory='{cache_prefix}'")

        self._tables = dict()

    def _gen_name(self, name: str):
        return f"{self._name}_{name}" if self._name is not None else name

    def sort(self, by: str | list | None, ascending: bool | list | None = None):
        self._sort_by = by
        
        if ascending is None:
            ascending = True
        self._ascending = ascending 
        
        if self._sort_by is not None:
            self._sort_by_ddb = get_ddb_sort_str(sort_by=by, ascending=ascending)
        
        return self

    def distinct(self, value: bool | None):
        if value is None:
            value = False
        self._distinct = value
            
        return self

    def drop(self, columns: str | list | None):
        self._drop = columns
        return self
        
    def _to_cache(self):
        s5 = S5CMD()
        copy_to_tmp_directory(
                src=self._path_org,
                dest=self._path_cache,
                filesystem=self._filesystem,
            )
        
        self._path = self._path_cache
        self._cached = True

    

    def _load_feather(self, **kwargs):
        if self._path_exists:
            if self._is_file:
                if self._filesystem is not None:
                    with open_(self._path, self._filesystem) as f:
                        self._mem_table = pf.read_feather(f, **kwargs)
                else:
                    self._mem_table = pf.read_feather(self._path, **kwargs)
            else:
                if not hasattr(self, "_dataset"):
                    self.set_dataset()
                self._mem_table = self._dataset.to_table(**kwargs)

    def _load_parquet(self, **kwargs):
        if self._path_exists:
            self._mem_table = pq.read_table(
                self._path,
                partitioning=self._partitioning,
                filesystem=self._filesystem,
                **kwargs,
            )

    def _load_csv(self, **kwargs):

        pass
    
    def set_dataset(self, name: str = "dataset", **kwargs):
        if self._caching and not self._cached:
            self._to_cache()
            
        name = self._gen_name(name)

        if self._path_exists:
            self._dataset = ds.dataset(
                source=self._path,
                format=self._format,
                filesystem=self._filesystem,
                partitioning=self._partitioning,
                **kwargs,
            )

            # self._dataset = name
            self._tables["dataset"] = name
            self.ddb.register(name, self._dataset)

    def load_mem_table(
        self,
        name: str = "mem_table",
        sort_by: str | list | None = None,
        ascending: bool | list | None = None,
        distinct: bool | None = None,
        drop: str | list | None = None,
        **kwargs,
    ):
        if self._caching and not self._cached:
            self._to_cache()
            
        name = self._gen_name(name)

        if sort_by is not None:
            self.sort(by=sort_by, ascending=ascending)

        if distinct is not None:
            self.distinct(distinct)

        if drop is not None:
            self.drop(columns=drop)

        if self._format == "parquet":
            self._load_parquet(**kwargs)

        elif (
            self._format == "feather"
            or self._format == "ipc"
            or self._format == "arrow"
        ):
            self._load_feather(**kwargs)

        elif self._format == "csv":
            self._load_csv(**kwargs)

        self._mem_table = sort_table(
            drop_columns(self._mem_table, columns=self._drop),
            sort_by=self._sort_by,
            ascending=self._ascending,
        )

        if self._distinct:
            self._mem_table = distinct_table(self._mem_table)

        self._tables["mem_table"] = name
        self.ddb.register(name, self._mem_table)
                          

    def create_temp_table(
        self,
        name: str = "temp_table",
        sort_by: str | list | None = None,
        ascending: bool | list | None = None,
        distinct: bool = False,
        drop: str | list | None = None,
    ):
        if self._caching and not self._cached:
            self._to_cache()
            
        name = self._gen_name(name)

        if sort_by is not None:
            self.sort(by=sort_by, ascending=ascending)

        if distinct:
            self.distinct(distinct)

        if drop is not None:
            self.drop(columns=drop)

        if self.has_mem_table:

            self._mem_table = sort_table(
                drop_columns(self._mem_table, columns=self._drop),
                sort_by=self._sort_by,
                ascending=self._ascending,
            )
            if distinct:
                self._mem_table = distinct_table(self._mem_table)

            sql = f"CREATE OR REPLACE TEMP TABLE {name} AS  SELECT * FROM {self._tables['mem_table']}"

        else:
            if not hasattr(self, "_dataset"):
                self.set_dataset()

            sql = f"CREATE OR REPLACE TEMP TABLE {name} AS  SELECT * FROM {self._tables['dataset']}"

            if sort_by is not None:

                sort_by = get_ddb_sort_str(sort_by=sort_by, ascending=ascending)

                sql += f" ORDER BY {sort_by}"

            if drop is not None:
                if isinstance(drop, str):
                    drop = [drop]
                    drop = [col for col in drop if col in self._dataset.schema.names]

                sql = sql.replace("SELECT *", f"SELECT * exclude({','.join(drop)})")

            if distinct:
                self.distinct(distinct)
                sql = sql.replace("SELECT *", "SELECT DISTINCT *")

        self._tables["temp_table"] = name
        self.ddb.execute(sql)

    def to_relation(
        self,
        create_temp_table: bool = False,
        sort_by: str | list | None = None,
        ascending: bool | list | None = None,
        distinct: bool = False,
        drop: str | list | None = None,
    ):
        if self._caching and not self._cached:
            self._to_cache()
            
        if sort_by is not None:
            self.sort(by=sort_by, ascending=ascending)

        if distinct:
            self.distinct(distinct)

        if drop is not None:
            self.drop(columns=drop)

        if create_temp_table:
            self.create_temp_table(sort_by=sort_by, distinct=distinct)

        if self.has_mem_table:
            self._rel = to_relation(
                table=self._mem_table,
                ddb=self.ddb,
                sort_by=self._sort_by,
                ascending=self._ascending,
                distinct=self._distinct,
                drop=self._drop,
            )

        elif self.has_temp_table:

            self._rel = self.ddb.query(f"SELECT * FROM {self._tables['temp_table']}")

            if distinct:
                self._rel = self._rel.distinct()

            if drop is not None:
                self._rel = drop_columns(self._rel, columns=self._drop)

            if sort_by is not None:
                self._rel.order(self._sort_by_ddb)

        else:
            if not self.has_dataset:
                self.set_dataset()

            self._rel = to_relation(
                table=self._dataset,
                ddb=self.ddb,
                sort_by=self._sort_by,
                ascending=self._ascending,
                distinct=self._distinct,
                drop=self._drop,
            )

        return self._rel

    def to_polars(
        self,
        sort_by: str | list | None = None,
        ascending: bool | list | None = None,
        distinct: bool | None = None,
        drop: str | list | None = None,
    ):
        if self._caching and not self._cached:
            self._to_cache()
            
        self.sort(by=sort_by, ascending=ascending)
        self.drop(drop)

        if self.has_mem_table:
            table = self._mem_table

        elif self.has_temp_table:
            sql = f"SELECT * FROM {self._tables['temp_table']}"

            if sort_by is not None:

                sort_by = get_ddb_sort_str(sort_by=sort_by, ascending=ascending)

                sql += f" ORDER BY {sort_by}"

            if drop is not None:
                if isinstance(drop, str):
                    drop = [drop]
                    drop = [
                        f"'{col}'" if " " in col else col
                        for col in drop
                        if col in self._dataset.schema.names
                    ]

                sql = sql.replace("SELECT *", f"SELECT * exclude({','.join(drop)})")

            if distinct:
                self.distinct(distinct)
                sql = sql.replace("SELECT *", "SELECT DISTINCT *")

            table = self.ddb.execute(sql).arrow()
        else:
            table = self._rel

        self._pl_dataframe = sort_table(
            drop_columns(to_polars(table=table), columns=drop),
            sort_by=self._sort_by,
            ascending=self._ascending,
        )

        if distinct:
            self._pl_dataframe = distinct_table(table=self._pl_dataframe)

        return self._pl_dataframe

    def to_pandas(
        self,
        sort_by: str | list | None = None,
        ascending: bool | list | None = None,
        distinct: bool | None = None,
        drop: str | list | None = None,
    ):
        if self._caching and not self._cached:
            self._to_cache()
            
        self.sort(by=sort_by, ascending=ascending)
        self.drop(drop)

        if self.has_mem_table:
            table = self._mem_table

        elif self.has_temp_table:
            sql = f"SELECT * FROM {self._tables['temp_table']}"

            if sort_by is not None:

                sort_by = get_ddb_sort_str(sort_by=sort_by, ascending=ascending)

                sql += f" ORDER BY {sort_by}"

            if drop is not None:
                if isinstance(drop, str):
                    drop = [drop]
                    drop = [
                        f"'{col}'" if " " in col else col
                        for col in drop
                        if col in self._dataset.schema.names
                    ]

                sql = sql.replace("SELECT *", f"SELECT * exclude({','.join(drop)})")

            if distinct:
                self.distinct(distinct)
                sql = sql.replace("SELECT *", "SELECT DISTINCT *")

            table = self.ddb.execute(sql).arrow()
        else:
            table = self._rel

        self._pd_dataframe = sort_table(
            drop_columns(to_pandas(table=table), columns=drop),
            sort_by=self._sort_by,
            ascending=self._ascending,
        )

        if distinct:
            self._pd_dataframe = distinct_table(table=self._pd_dataframe)

        return self._pd_dataframe

    def execute(self, *args, **kwargs):
        return self.ddb.execute(*args, **kwargs)

    def query(self, *args, **kwargs):
        return self.ddb.query(*args, **kwargs)

    @property
    def dataset(self) -> ds.FileSystemDataset:
        if not self.has_dataset:
            self.set_dataset()

        return self._dataset

    @property
    def mem_table(self) -> pa.Table:
        if not hasattr(self, "_mem_table"):
            if self.ddb is not None:
                if self.has_temp_table:
                    self._mem_table = self.ddb.query(
                        f"SELECT * FROM {self._tables['temp_table']}"
                    ).arrow()
                else:
                    self.load_mem_table()
            else:
                self.load_mem_table()

        return self._mem_table

    @property
    def rel(self) -> duckdb.DuckDBPyRelation:
        if not self.has_relation:
            self.to_relation()

        return self._rel
    
    @property
    def table(self) -> duckdb.DuckDBPyRelation:
        if not self.has_relation:
            self.to_relation()

        return self._rel

    @property
    def pl_dataframe(self) -> pl.DataFrame:
        if not self.has_pl_dataframe:
            self.to_polars()
        return self._pl_dataframe

    @property
    def pd_dataframe(self) -> pd.DataFrame:
        if not self.has_pd_dataframe:
            self.to_pandas()
        return self._pd_dataframe

    @property
    def has_temp_table(self) -> bool:
        return "temp_table" in self._tables

    @property
    def has_mem_table(self) -> bool:
        return "mem_table" in self._tables

    @property
    def has_dataset(self) -> bool:
        return "dataset" in self._tables

    @property
    def has_relation(self) -> bool:
        return hasattr(self, "_rel")

    @property
    def has_pl_dataframe(self) -> bool:
        return hasattr(self, "_pl_dataframe")

    @property
    def has_pd_dataframe(self) -> bool:
        return hasattr(self, "_pd_dataframe")
    
    @property
    def has_local(self) -> bool:
        return self._has_local