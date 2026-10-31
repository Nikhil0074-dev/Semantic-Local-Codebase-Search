# Supported languages

| Language | Extensions | Method | Confidence |
|---|---|---|---|
| Python | .py .pyi | stdlib `ast` (exact); decorators/comments included; syntax errors fall back to line windows | Tier 1 — tested |
| JavaScript | .js .jsx .mjs .cjs | heuristic scanner (functions, arrow functions, classes, methods) | Tier 1 — tested |
| TypeScript | .ts .tsx | as JS + interfaces/enums | Tier 1 — tested |
| Go | .go | funcs, receiver methods (`Type.Method`), struct/interface | Tier 1 — tested |
| Java | .java | classes/interfaces/enums/records, methods | Tier 1 — tested |
| SQL | .sql | statement splitter (strings, comments, `$$` quoting); CREATE objects named | Tier 1 — tested |
| Rust, C#, PHP, C, C++ | .rs .cs .php .c .h .cc .cpp .hpp | heuristic scanner | Tier 2 — one snippet test each |
| YAML/TOML/INI/JSON | .yaml .yml .toml .ini .cfg .json | 60-line windows with overlap | text only |

Everything else is ignored (counted, not listed). **Known limits of the heuristic scanner:** multi-line method signatures in
JS/TS class bodies, C/C++ macros, unusual formatting, nested/anonymous functions (treated as part of their parent). Misses
degrade to line windows; they never abort indexing. Planned: Tree-sitter.
Generated, minified, binary, lockfile, symlinked, oversized (>1 MB default) files are skipped and reported.
