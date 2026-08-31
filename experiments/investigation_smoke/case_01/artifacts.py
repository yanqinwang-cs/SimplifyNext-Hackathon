from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree


MAIN_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _column_number(reference: str) -> int:
    letters = "".join(character for character in reference if character.isalpha())
    number = 0
    for character in letters:
        number = number * 26 + ord(character.upper()) - ord("A") + 1
    return number


def _cell_value(cell, shared_strings: list[str]) -> str:
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.iter(MAIN_NS + "t"))
    value = cell.find(MAIN_NS + "v")
    if value is None or value.text is None:
        return ""
    if cell_type == "s":
        return shared_strings[int(value.text)]
    return value.text


def _render_sheet(xml: bytes, shared_strings: list[str]) -> list[str]:
    root = ElementTree.fromstring(xml)
    rendered: list[str] = []
    for row in root.findall(f"{MAIN_NS}sheetData/{MAIN_NS}row"):
        cells = row.findall(MAIN_NS + "c")
        if not cells:
            continue
        values = [""] * max(_column_number(cell.attrib["r"]) for cell in cells)
        for cell in cells:
            values[_column_number(cell.attrib["r"]) - 1] = _cell_value(cell, shared_strings)
        while values and values[-1] == "":
            values.pop()
        if values:
            rendered.append("\t".join(values))
    return rendered


def render_xlsx(path: str | Path) -> str:
    """Render every non-empty row of every worksheet deterministically."""
    with ZipFile(path) as workbook:
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
            for item in root.findall(MAIN_NS + "si"):
                shared_strings.append("".join(node.text or "" for node in item.iter(MAIN_NS + "t")))
        workbook_root = ElementTree.fromstring(workbook.read("xl/workbook.xml"))
        relationships = ElementTree.fromstring(workbook.read("xl/_rels/workbook.xml.rels"))
        targets = {
            relationship.attrib["Id"]: relationship.attrib["Target"]
            for relationship in relationships
        }
        sheets: list[str] = []
        for sheet in workbook_root.findall(f"{MAIN_NS}sheets/{MAIN_NS}sheet"):
            name = sheet.attrib["name"]
            target = targets[sheet.attrib[REL_NS + "id"]].lstrip("/")
            if not target.startswith("xl/"):
                target = "xl/" + target
            rows = _render_sheet(workbook.read(target), shared_strings)
            sheets.append(f"SHEET: {name}\n" + "\n".join(rows))
        return "\n\n".join(sheets)


def render_artifact(path: str | Path) -> str:
    path = Path(path)
    if path.suffix.lower() == ".xlsx":
        return render_xlsx(path)
    return path.read_text(encoding="utf-8")
