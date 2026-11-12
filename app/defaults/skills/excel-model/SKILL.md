---
name: excel-model
description: Build or update an Excel model (budget, forecast, pricing, unit economics) with live formulas, clear inputs and a check that every formula calculates.
---

# Excel model

1. Find the inputs. Read any files the user named (Excel, CSV, PDF) with read_file or read_document. Ask one question only if a number that drives the whole model is missing; otherwise state your assumption.
2. Plan the workbook before building it:
   - An Inputs sheet with every assumption in its own labeled cell, units in the label.
   - Calculation sheets that only reference inputs and other calculations, never typed numbers.
   - A Summary sheet with the answers the user asked for and a small chart if it helps.
3. Build it with make_spreadsheet (Meshpool's sheets engine), describing the sheets, rows, columns and formulas you planned. To update an existing workbook, say which file and which cells change.
4. Check the result: read the new file back, confirm totals tie out and no cell shows an error such as #REF! or #DIV/0!.
5. Reply with where the file is, the three or four numbers that matter, and the assumptions the user should check.
