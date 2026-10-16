# Power BI semantic model — metadata queries

Copy-paste queries to document a semantic model's structure from inside Power BI Desktop,
without exporting any data. They return only metadata (table, column, relationship and
measure definitions) — never row values.

## How to run

1. Open the `.pbix` in Power BI Desktop.
2. Switch to **DAX query view** (left rail, 4th icon).
3. Paste one query, press **Run** (or F5).
4. Zoom the result grid (`Ctrl` + `+`) and photograph it; overlap each scroll by a row or two.

Start with query 1 (usually fits in one or two screens), then query 2.

## 1 — Relationships

One readable line per relationship (from/to table and column, cardinality, filter direction).

```dax
EVALUATE
SELECTCOLUMNS(
    INFO.VIEW.RELATIONSHIPS(),
    "Relationship", [Relationship],
    "Active", [IsActive]
)
ORDER BY [Relationship]
```

## 2 — Columns per table

```dax
EVALUATE
SELECTCOLUMNS(
    INFO.VIEW.COLUMNS(),
    "Table", [Table],
    "Column", [Name],
    "Type", [DataType]
)
ORDER BY [Table], [Column]
```

## 3 — Measures with DAX

Expressions that start with a line break (`Name =` then Enter) look empty in the result
grid, which only shows the first line of a cell. Line breaks and tabs are therefore
flattened to spaces; `Len` shows whether an expression exists at all.

```dax
EVALUATE
SELECTCOLUMNS(
    INFO.VIEW.MEASURES(),
    "Table", [Table],
    "Measure", [Name],
    "Len", LEN([Expression]),
    "DAX", TRIM(SUBSTITUTE(SUBSTITUTE(SUBSTITUTE(
        [Expression], UNICHAR(13), " "), UNICHAR(10), " "), UNICHAR(9), " "))
)
ORDER BY [Table], [Measure]
```

If `Len` is blank everywhere, try `EVALUATE INFO.MEASURES()` (no `.VIEW`), or read the
measures in **TMDL view** by dragging a measure table onto the editor.

## 4 — Tables

```dax
EVALUATE
INFO.VIEW.TABLES()
```

## Fallback

The column names in queries 2 and 3 are untested. If one fails with an unknown-column
error, run the function on its own and use whatever columns it returns:

```dax
EVALUATE INFO.VIEW.COLUMNS()
```

```dax
EVALUATE INFO.VIEW.MEASURES()
```

## Optional — where each table comes from

**TMDL view** (left rail, below DAX query view): drag a table onto the editor. Its
`partition` block shows the Power Query (M) source, i.e. which lakehouse table the model
reads. Useful for mapping model tables to bronze / silver / gold.

Crop out server, host and HTTP-path lines before photographing — they are not needed.
