# Third-party notices

EVE Trader itself is released under the [MIT license](LICENSE). This file lists
material in or used by this repository that is **not** covered by that license,
or that is covered by a license with conditions of its own.

## PI reference layouts (`eve_trader/pi/layout/reference_templates.json`)

About 410 Planetary Industry colony layouts were collected from the public
community site planetsin.space in October 2026. Comments and author names were
removed, only layouts that the built-in validator accepts were kept, and
duplicates were merged. The file is shipped as package data and used by
`eve_trader/pi/layout/reference.py` as a source of examples.

These layouts were created by the community members who shared them. They are
**not covered by the MIT license** of this repository, and no separate license
was found for them. They are included only as examples for the layout generator.

## CCP Games data and trademarks

Game data used by this tool (the ESI API and the Static Data Export) belongs to
CCP hf. and is used under the
[CCP developer license agreement](https://developers.eveonline.com/license-agreement).
EVE Online, the EVE logo, EVE and all associated logos and designs are the
intellectual property of CCP hf. EVE Trader is an unofficial third-party tool,
not affiliated with or endorsed by CCP hf.

## Dependencies with licenses other than MIT / BSD

Python (see `requirements.txt` for exact versions):

| Package | License | Note |
|---|---|---|
| psycopg, psycopg-binary, psycopg-pool | LGPL-3.0 | used unmodified as a library |
| certifi | MPL-2.0 | unmodified |
| cryptography | Apache-2.0 OR BSD-3-Clause | token encryption |

Frontend (see `frontend/package.json`):

| Package | License | Note |
|---|---|---|
| dompurify | MPL-2.0 OR Apache-2.0 | sanitises mail bodies |
| lightningcss | MPL-2.0 | build time only, not shipped |
| @fontsource/rajdhani, @fontsource/inter, @fontsource/jetbrains-mono | OFL-1.1 | font files bundled into the built frontend |

The three typefaces (Rajdhani, Inter, JetBrains Mono) are licensed under the
SIL Open Font License 1.1 (https://openfontlicense.org). The fonts are bundled
unmodified; each package in `node_modules/@fontsource/` carries its own
`LICENSE` file.

Other dependencies are under permissive licenses (MIT, BSD, Apache-2.0, ISC) and
are listed with their licenses by `pip-licenses` and `npm ls --all`.
