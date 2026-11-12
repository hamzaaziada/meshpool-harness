---
name: power-bi-review
description: Review a Power BI file (.pbix or .pbit): pages and visuals, tables, relationships, DAX measures and Power Query, with fixes for slow or wrong measures.
---

# Power BI review

1. Read the file with read_document (or read_file in Meshpool Code). It returns the report pages and visuals, the data model tables and columns, relationships, DAX measures, calculated columns, Power Query steps and sample rows.
2. Check the model:
   - Relationships: missing or many-to-many joins, bidirectional filters that are not needed, a date table that is not marked or not related.
   - DAX: measures that use FILTER over a whole table where CALCULATE with a column filter works, iterators over large tables, divisions without DIVIDE, hard-coded values.
   - Power Query: steps that break query folding, columns loaded but never used.
   - Report: visuals with too many fields, pages with no clear question, inconsistent number formats.
3. Report findings ranked by impact, each with the table or measure name and a corrected DAX or M snippet.
4. If the user asks for a document, save the review with make_document or make_pdf.
