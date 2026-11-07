# Commercial licensing

Tally Local is licensed under the **PolyForm Noncommercial License 1.0.0** (see `LICENSE`).

That license covers personal use: your own finances, hobby projects, study, research, and use by
charities, schools, public research bodies and government institutions.

It does **not** cover commercial use. Commercial use includes, for example:

- running Tally Local as part of a business, or for a company's finances
- offering Tally Local (or a modified version) as a hosted or managed service
- bundling Tally Local into a paid product, or shipping it in commercial hardware
- internal commercial tooling built on Tally Local's code or its API/MCP server

If you need any of those, a commercial licence is available. It is a plain paid licence with no
copyleft obligations and no requirement to publish your changes.

**Contact:** open an issue at <https://github.com/cryptobredda/tallylocal/issues> with the subject
"Commercial licence", or email the maintainer listed on the GitHub profile.

## Why this licence

The goal is simple: individuals should get a great private finance tool for free, and anyone making
money from it should contribute back. PolyForm Noncommercial does that in one document, without the
ambiguity of "open core" editions.

## A note on "open source"

PolyForm Noncommercial is **source-available**, not OSI-approved open source, because it restricts
commercial use. That is intentional. It also means:

- GitHub will show the repository as "source-available" rather than "open source".
- Some package indexes and "awesome" lists may not accept it.
- If you later want maximum ecosystem adoption, dual-licensing under AGPL-3.0 + commercial is the
  alternative path. The code is structured so the change is a licence-file change, not a rewrite.

## Third-party components

Tally Local depends on permissively licensed libraries (Flask BSD-3-Clause, SQLAlchemy MIT,
pdfplumber MIT, MCP Python SDK MIT, ReportLab BSD, PaddleOCR Apache-2.0, and others). Their licences
continue to apply to their code; this licence applies to Tally Local's own code.
