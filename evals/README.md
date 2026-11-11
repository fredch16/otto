# OTTO evaluation questions

Private, ignored `*.yaml` files in this directory hold real questions for OTTO
with answers checked against the source documents. Use them to compare models,
clients and changes to the MCP servers on the same questions.

Each entry records:

- `question`: asked exactly as Fred would ask it.
- `sources`: documents (title, id, pages) a correct answer must use.
- `must_include`: facts a correct answer states.
- `must_flag`: gaps, ambiguities or missing documents it must point out.
- `must_not`: claims that would be wrong or unsupported.
