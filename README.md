# needs-reqdiff

`needs-reqdiff` vergleicht zwei Verzeichnisbäume mit `sphinx-needs`-JSON-Exports und erzeugt daraus RST-Reports.

## Funktionen

- rekursives Einlesen von JSON-Dateien aus zwei Ordnern
- Vergleich von Dateien mit gleichem relativen Pfad
- Erkennen von hinzugekommenen, geänderten und entfernten Needs
- Erkennen von Dateien, die nur links oder nur rechts vorkommen
- Behandlung ungültiger JSON-Dateien mit Fehlerbericht
- Generierung von `sphinx-needs`-Importdateien und `.rst`-Reports
- Side-by-side-Darstellung geänderter Needs

## Paket bauen

Mit uv im PATH, unter Linux und Windows:

```bash
python build_package.py
```

Der Build erzeugt in `dist/` ein Quellarchiv (`.tar.gz`) und baut daraus
das plattformunabhängige Wheel (`.whl`). Im Projektverzeichnis geht das
auch direkt mit `uv build`.

## Installation

```bash
pip install -e .
```

Für eine Installation aus dem gebauten Wheel:

```bash
uv pip install dist/needs_reqdiff-<VERSION>-py3-none-any.whl
```

## Aufruf

```bash
needs-reqdiff path/to/baseline path/to/candidate path/to/output
```

Optional:

```bash
needs-reqdiff baseline candidate output --include "**/*.json" --exclude "**/archive/*.json"
```

## Sphinx-Einbindung

Im Sphinx-Projekt muss `sphinx_needs` aktiviert sein:

```python
extensions = ["sphinx_needs", "needs_reqdiff"]
```

Die generierte `index.rst` kann dann in eine bestehende Toctree eingebunden werden.

## Testen

```bash
pytest
```
