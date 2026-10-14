# Third-party notices

## TreeTrace_Douglas dataset

The repository does not commit the complete TreeTrace source tables. The optional development bootstrap downloads the V1 tables from the official repository and verifies fixed hashes before use. The repository includes a five-column, 346-row geometry derivative and a deterministic 80-row bounded demo subset in `sample-data/treetrace/` so reviewers can exercise the simple upload workflow without a separate dataset download.

- Dataset: `TreeTrace_Douglas`
- Persistent identifier: `doi:10.15454/YUNEGL`
- Official page: <https://entrepot.recherche.data.gouv.fr/dataset.xhtml?persistentId=doi%3A10.15454%2FYUNEGL>
- Terms recorded during the source audit: Creative Commons Attribution 2.0 (`CC BY 2.0`), with attribution requested in the format provided on the dataset page

The derivative keeps only board identity, length, cross-section dimensions, and dataset-level species. Metres are converted to millimetres and `boardID` is normalized to `TT-B###`. It does not include destructive-test outcomes or claim structural eligibility, live availability, reclaimed status, or engineering approval. Its source hash, generated hash, attribution, and rebuild instructions are recorded in `sample-data/treetrace/`.

The MIT license in this repository applies to this project's code and documentation; it does not relicense third-party data. Anyone downloading or redistributing the dataset must follow the dataset's own terms and attribution requirements.

See [the sample-data documentation](sample-data/treetrace/README.md) for version, provenance, transformation rules, and hash details.
