# LicensedTO

Search Toronto's licensed businesses by name, address, category, postal code or licence number, filter by ward
and renewal date, and see them on a map.

Built by [Mojgan Samandar](https://samandarali.github.io/). Free to use, no account needed. Questions:
hello@pratechdigital.com.

Phone numbers from the City's file are deliberately left out, to protect sole proprietors who list a personal
number.

Contains information licensed under the
[Open Government Licence – Toronto](https://open.toronto.ca/open-data-license/) (City of Toronto Open Data,
Municipal Licensing & Standards, "Business Licences and Permits"). LicensedTO is not affiliated with or endorsed by
the City of Toronto. See [terms.html](terms.html) for the terms of use and privacy note.

The data updates itself every weekday at 10 pm Toronto time: a GitHub Actions job
([.github/workflows/update-data.yml](.github/workflows/update-data.yml)) downloads the City's daily file through the
open data API and applies what changed ([scripts/update-licensedto.py](scripts/update-licensedto.py)).

Inspired by [NowServingTO](https://nowservingto.com/) by Joshua Opolko.

